"""backup.py tests. `_rsync` is monkeypatched to a plain local `shutil.copytree` in every test
here -- rsync isn't installed in every dev environment this test suite runs in (confirmed: not
on Windows/Git Bash), and the real binary's own correctness is what the dedicated CI
restore-from-backup job exercises end-to-end instead (see .github/workflows/ci.yml). What's under
test here is create_backup/restore_backup's own orchestration: snapshot naming/ordering, local
retention pruning, and that a round trip through VACUUM INTO + copy actually preserves real data.
"""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Engine

from perseverer.backup import (
    BackupResult,
    _ensure_destination_exists,
    _is_remote,
    _prune_local_snapshots,
    _rsync,
    create_backup,
    restore_backup,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata


def _fake_rsync(
    src: str, dest: str, *, ssh_key_path: str | None = None, known_hosts_path: str | None = None
) -> None:
    """Stands in for the real `rsync -a src/ dest/` -- copies src's *contents* into dest,
    creating dest and merging with anything already there, matching rsync's own trailing-slash
    semantics closely enough for these orchestration-level tests.

    Deliberately mimics one real, easy-to-miss rsync limitation exactly (confirmed live against
    the real binary, not assumed): rsync only auto-creates the *last* missing path component of
    a destination, not every missing parent -- `dest_path.mkdir(parents=True, ...)` would be
    more forgiving than the real thing and let a real bug (create_backup rsyncing into a
    destination root that doesn't exist yet at all) pass silently here while still failing for
    real. Only the immediate parent is required to pre-exist, same as real rsync."""
    src_path, dest_path = Path(src), Path(dest)
    dest_path.mkdir(exist_ok=True)
    if src_path.is_dir():
        shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
    elif src_path.exists():
        shutil.copy2(src_path, dest_path / src_path.name)


@pytest.fixture(autouse=True)
def _patch_rsync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("perseverer.backup._rsync", _fake_rsync)


def _seeded_engine(tmp_path: Path, db_name: str = "db.sqlite") -> Engine:
    engine = make_engine(tmp_path / db_name)
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id="ath1",
                display_name="Jerome",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
                username="jerome",
                password_hash=None,
            )
        )
        conn.commit()
    return engine


def test_rsync_command_includes_ssh_key_and_known_hosts_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct test of _rsync's own command construction (not the _fake_rsync stand-in the rest
    of this file uses) -- this is the one place worth verifying the actual `-e ssh ...` flag
    string, since a typo there would only ever surface against a real rsync binary."""
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], check: bool) -> None:
        captured["cmd"] = cmd

    monkeypatch.setattr("perseverer.backup.subprocess.run", fake_run)

    _rsync(
        "/local/src",
        "user@host:/remote/dest",
        ssh_key_path="/data/backup_key",
        known_hosts_path="/data/backup_known_hosts",
    )

    cmd = captured["cmd"]
    assert cmd[:2] == ["rsync", "-a"]
    assert cmd[-2:] == ["/local/src/", "user@host:/remote/dest/"]
    ssh_opts = cmd[cmd.index("-e") + 1]
    assert "BatchMode=yes" in ssh_opts
    assert "StrictHostKeyChecking=accept-new" in ssh_opts
    assert "-i /data/backup_key" in ssh_opts
    assert "-o UserKnownHostsFile=/data/backup_known_hosts" in ssh_opts


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("user@bercy.local:/srv/backups/perseverer", True),
        ("/data/local-backups", False),
        ("C:/Users/jerome/backups", False),  # a Windows drive letter's ':' is not user@host's
    ],
)
def test_is_remote(spec: str, expected: bool) -> None:
    assert _is_remote(spec) == expected


def test_ensure_destination_exists_creates_missing_local_dir_with_all_parents(
    tmp_path: Path,
) -> None:
    target = tmp_path / "does" / "not" / "exist" / "yet"
    assert not target.exists()
    _ensure_destination_exists(str(target), ssh_key_path=None, known_hosts_path=None)
    assert target.is_dir()


def test_ensure_destination_exists_runs_mkdir_p_over_ssh_for_a_remote_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], check: bool) -> None:
        captured["cmd"] = cmd

    monkeypatch.setattr("perseverer.backup.subprocess.run", fake_run)

    _ensure_destination_exists(
        "user@host:/srv/backups/perseverer",
        ssh_key_path="/data/backup_key",
        known_hosts_path="/data/backup_known_hosts",
    )

    cmd = captured["cmd"]
    assert cmd[0] == "ssh"
    assert cmd[-2] == "user@host"
    assert cmd[-1] == "mkdir -p /srv/backups/perseverer"


def test_create_backup_succeeds_when_the_destination_root_does_not_exist_at_all(
    tmp_path: Path,
) -> None:
    """The real regression this guards: a fresh PERSEVERER_BACKUP_PATH that's never received a
    backup before doesn't just have an empty `raw`/`parquet`/`db` subdirectory missing -- the
    root itself is missing, two path levels short of what a plain rsync call can create in one
    step (confirmed live against the real binary, see _ensure_destination_exists's docstring)."""
    engine = _seeded_engine(tmp_path)
    (tmp_path / "raw").mkdir()
    (tmp_path / "parquet").mkdir()

    # Two levels missing under tmp_path, not one -- "remote" itself doesn't exist yet either.
    dest = tmp_path / "remote" / "backups" / "perseverer"
    result = create_backup(
        engine,
        tmp_path / "raw",
        tmp_path / "parquet",
        tmp_path / "backups",
        destination=str(dest),
    )

    assert result is not None
    assert (dest / "db" / result.snapshot_path.name).exists()


def test_create_backup_skipped_without_destination_or_config(tmp_path: Path) -> None:
    engine = _seeded_engine(tmp_path)
    result = create_backup(
        engine, tmp_path / "raw", tmp_path / "parquet", tmp_path / "backups"
    )
    assert result is None
    assert not (tmp_path / "backups").exists()


def test_create_backup_writes_a_real_readable_snapshot(tmp_path: Path) -> None:
    engine = _seeded_engine(tmp_path)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "ab").mkdir()
    (tmp_path / "raw" / "ab" / "abcd.gz").write_bytes(b"fake-raw-blob")
    (tmp_path / "parquet").mkdir()
    (tmp_path / "parquet" / "a1.parquet").write_bytes(b"fake-parquet")

    dest = tmp_path / "remote"
    result = create_backup(
        engine,
        tmp_path / "raw",
        tmp_path / "parquet",
        tmp_path / "backups",
        destination=str(dest),
    )

    assert isinstance(result, BackupResult)
    assert result.snapshot_path.exists()
    # The VACUUM INTO'd file is a real, independently-readable SQLite DB with the same row.
    raw_conn = sqlite3.connect(result.snapshot_path)
    row = raw_conn.execute("SELECT username FROM athlete WHERE id = 'ath1'").fetchone()
    raw_conn.close()
    assert row == ("jerome",)

    assert (dest / "raw" / "ab" / "abcd.gz").read_bytes() == b"fake-raw-blob"
    assert (dest / "parquet" / "a1.parquet").read_bytes() == b"fake-parquet"
    assert (dest / "db" / result.snapshot_path.name).exists()


def test_create_backup_prunes_local_snapshots_beyond_keep(tmp_path: Path) -> None:
    engine = _seeded_engine(tmp_path)
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    for ts in ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"]:
        (backups_dir / f"perseverer-{ts}.db").write_bytes(b"old")

    result = create_backup(
        engine,
        tmp_path / "raw",
        tmp_path / "parquet",
        backups_dir,
        destination=str(tmp_path / "remote"),
        keep_local_snapshots=2,
    )

    assert result is not None
    remaining = sorted(p.name for p in backups_dir.glob("perseverer-*.db"))
    # 3 pre-existing + 1 new = 4, keep=2 -> pruned 2, and the newest (just-created) one survives.
    assert len(remaining) == 2
    assert result.snapshot_path.name in remaining
    assert result.pruned_local_snapshots == 2


@pytest.mark.parametrize(
    ("keep", "expected_remaining"),
    [
        (2, ["b", "c"]),
        (0, []),
        (10, ["a", "b", "c"]),
    ],
)
def test_prune_local_snapshots_keep_values(
    tmp_path: Path, keep: int, expected_remaining: list[str]
) -> None:
    for name in ["a", "b", "c"]:
        (tmp_path / f"perseverer-{name}.db").write_bytes(b"x")
        # Ensure distinct sort order regardless of filesystem mtime granularity.
    # Rename with real sortable timestamps so ordering is unambiguous.
    (tmp_path / "perseverer-a.db").rename(tmp_path / "perseverer-20260101T000000Z.db")
    (tmp_path / "perseverer-b.db").rename(tmp_path / "perseverer-20260102T000000Z.db")
    (tmp_path / "perseverer-c.db").rename(tmp_path / "perseverer-20260103T000000Z.db")
    mapping = {"a": "20260101T000000Z", "b": "20260102T000000Z", "c": "20260103T000000Z"}

    _prune_local_snapshots(tmp_path, keep=keep)

    remaining = {p.stem.removeprefix("perseverer-") for p in tmp_path.glob("perseverer-*.db")}
    assert remaining == {mapping[name] for name in expected_remaining}


def test_restore_backup_round_trip(tmp_path: Path) -> None:
    engine = _seeded_engine(tmp_path)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "cd").mkdir()
    (tmp_path / "raw" / "cd" / "cdef.gz").write_bytes(b"real-raw-content")
    (tmp_path / "parquet").mkdir()
    (tmp_path / "parquet" / "act1.parquet").write_bytes(b"real-parquet-content")

    dest = tmp_path / "remote"
    create_backup(
        engine,
        tmp_path / "raw",
        tmp_path / "parquet",
        tmp_path / "backups",
        destination=str(dest),
    )

    # Simulate total local loss: wipe the DB and the raw/parquet trees. dispose() first --
    # SQLite keeps the file handle open via the engine's connection pool, which blocks deletion
    # on Windows (posix silently allows unlinking an open file; Windows doesn't).
    engine.dispose()
    (tmp_path / "db.sqlite").unlink()
    shutil.rmtree(tmp_path / "raw")
    shutil.rmtree(tmp_path / "parquet")

    result = restore_backup(
        str(dest),
        db_path=tmp_path / "db.sqlite",
        raw_archive_dir=tmp_path / "raw",
        parquet_dir=tmp_path / "parquet",
    )

    assert result.restored_db_path == tmp_path / "db.sqlite"
    conn = sqlite3.connect(result.restored_db_path)
    row = conn.execute("SELECT username FROM athlete WHERE id = 'ath1'").fetchone()
    conn.close()
    assert row == ("jerome",)
    assert (tmp_path / "raw" / "cd" / "cdef.gz").read_bytes() == b"real-raw-content"
    assert (tmp_path / "parquet" / "act1.parquet").read_bytes() == b"real-parquet-content"


def test_restore_backup_raises_when_source_has_no_snapshot(tmp_path: Path) -> None:
    empty_source = tmp_path / "empty-remote"
    (empty_source / "db").mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        restore_backup(
            str(empty_source),
            db_path=tmp_path / "db.sqlite",
            raw_archive_dir=tmp_path / "raw",
            parquet_dir=tmp_path / "parquet",
        )
