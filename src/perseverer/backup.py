"""Backup + restore automation.

Backs up the SQLite database (via `VACUUM INTO` -- a live, consistent snapshot that needs no app
downtime, unlike stopping the app to copy the file) plus the raw archive and Parquet trees (via
rsync, whose own delta-transfer means the bulk of a re-run -- immutable, content-addressed
`raw_object` blobs -- costs nothing) to a second host over SSH.

Deliberately NOT wired into the Settings web UI's job-trigger pattern (`api/routers/settings.py`
uses `BackgroundTasks` for rebuild/sync): a restore overwrites live data with an older snapshot --
genuinely destructive, rare, ops-only, unlike rebuild which only ever replays and is additive over
the raw archive. `restore_backup` stays CLI/SSH-only (`sync backup restore`), matching
`adapters/garmin_connect.py::login_with_credentials`'s own "human-initiated, one-shot" precedent
for dangerous actions.

`create_backup`'s `destination` and `restore_backup`'s `source` are both a plain string --
`user@host:path` or a bare local path -- because rsync treats the two identically. That symmetry
is what lets the CI restore-from-backup test call these exact same functions against a local temp
directory instead of a real SSH host: the only thing under test is "does a snapshot actually
contain everything needed to restore," not "can CI reach a LAN host it was never going to have
network access to anyway."
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

logger = logging.getLogger(__name__)

_SNAPSHOT_PREFIX = "perseverer-"
_SNAPSHOT_SUFFIX = ".db"


@dataclass
class BackupResult:
    snapshot_path: Path
    destination: str
    pruned_local_snapshots: int


@dataclass
class RestoreResult:
    restored_db_path: Path
    source_snapshot: str


def _snapshot_filename(now: datetime | None = None) -> str:
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{_SNAPSHOT_PREFIX}{ts}{_SNAPSHOT_SUFFIX}"


def _ssh_options(*, ssh_key_path: str | None, known_hosts_path: str | None) -> str:
    """The `-e` ssh command rsync uses, and the same options a plain `ssh` invocation needs
    (see `_ensure_destination_exists` below) -- run unattended from a cron job with no terminal
    attached: `BatchMode=yes` fails fast instead of hanging on a password/passphrase prompt, and
    `StrictHostKeyChecking=accept-new` trusts a host's key on first contact (so the very first
    scheduled run doesn't hang on an interactive "are you sure" prompt) while still refusing a
    *changed* key outright -- not a blanket disable of host-key verification.

    `known_hosts_path`, when given, points ssh at a specific file to record newly-trusted host
    keys into instead of its own default `~/.ssh/known_hosts`. This matters because the
    container hardening mounts the SSH key directory read-only -- `accept-new` still
    needs to *write* a first-contact host key somewhere, so that write has to land under a path
    that's actually writable (under `/data`) rather than the read-only key mount."""
    opts = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    if ssh_key_path:
        opts += f" -i {ssh_key_path}"
    if known_hosts_path:
        opts += f" -o UserKnownHostsFile={known_hosts_path}"
    return opts


def _is_remote(spec: str) -> bool:
    """`user@host:path` vs. a bare local path -- rsync's own heuristic (an `@` before the first
    `:`) is what this mirrors, since that's the one place local-vs-remote actually matters here:
    ensuring the destination root exists (see below), which needs a real `ssh` round-trip for a
    remote spec but a plain `mkdir` for a local one."""
    host_part = spec.split(":", 1)[0]
    return "@" in host_part


def _ensure_destination_exists(
    destination: str, *, ssh_key_path: str | None, known_hosts_path: str | None
) -> None:
    """rsync only auto-creates the *last* missing path component of a destination -- confirmed
    live (not assumed): syncing into `<dest>/raw` when `<dest>` itself doesn't exist yet fails
    outright (`mkdir ".../raw" failed: No such file or directory`), since two levels would need
    creating in one step. A backup destination that's never been used before is exactly this
    case (a fresh `PERSEVERER_BACKUP_PATH` on a host that's never received a backup), so every
    one of `create_backup`'s three `_rsync` calls needs the destination root to already exist
    first, locally or over SSH."""
    if _is_remote(destination):
        user_host, _, remote_path = destination.partition(":")
        ssh_cmd = shlex.split(
            _ssh_options(ssh_key_path=ssh_key_path, known_hosts_path=known_hosts_path)
        )
        subprocess.run([*ssh_cmd, user_host, f"mkdir -p {shlex.quote(remote_path)}"], check=True)
    else:
        Path(destination).mkdir(parents=True, exist_ok=True)


def _rsync(src: str, dest: str, *, ssh_key_path: str | None, known_hosts_path: str | None) -> None:
    """One `rsync -a <src>/ <dest>/` call. `src`/`dest` may each be a local path or a
    `user@host:path` remote spec -- rsync treats both the same way, which is the whole point (see
    module docstring)."""
    ssh_opts = _ssh_options(ssh_key_path=ssh_key_path, known_hosts_path=known_hosts_path)
    cmd = ["rsync", "-a", "-e", ssh_opts, f"{src}/", f"{dest}/"]
    subprocess.run(cmd, check=True)


def _prune_local_snapshots(backups_dir: Path, *, keep: int) -> int:
    """Deletes all but the `keep` newest local snapshot files. The filename timestamp format
    (%Y%m%dT%H%M%SZ) sorts lexically in chronological order, so a plain sort suffices. Only the
    *local* copy is pruned -- the remote destination is the real backup and is deliberately left
    to accumulate full history; local snapshots exist only to be rsync'd from, so there's no
    reason to let them grow the server's own disk usage unbounded.

    `keep <= 0` prunes everything -- guarded explicitly because `list[:-0]` is `list[:0]`, not
    "all but the last 0", a real Python footgun `-keep` would otherwise hit silently at keep=0.
    """
    snapshots = sorted(backups_dir.glob(f"{_SNAPSHOT_PREFIX}*{_SNAPSHOT_SUFFIX}"))
    to_prune = snapshots if keep <= 0 else snapshots[:-keep]
    for path in to_prune:
        path.unlink()
    return len(to_prune)


def create_backup(
    engine: Engine,
    raw_archive_dir: Path,
    parquet_dir: Path,
    backups_dir: Path,
    *,
    destination: str | None = None,
    backup_user: str | None = None,
    backup_host: str | None = None,
    backup_path: str | None = None,
    ssh_key_path: str | None = None,
    known_hosts_path: str | None = None,
    keep_local_snapshots: int = 7,
) -> BackupResult | None:
    """Snapshots the live DB and rsyncs it plus the raw archive + Parquet trees to `destination`
    (or, if not given, `backup_user@backup_host:backup_path` built from the other three args --
    the shape every scheduled/production call uses). Returns `None` (logs, doesn't raise) when
    neither resolves to a real destination -- same graceful-degradation contract as
    `adapters/eufy.py::sync_eufy` when its own credentials are unset, and what makes this safe to
    call unconditionally from the worker's daily schedule before backups are configured at all.
    """
    if destination is None:
        if not (backup_user and backup_host and backup_path):
            logger.info(
                "backup skipped: no destination given and "
                "PERSEVERER_BACKUP_HOST/USER/PATH are not all configured"
            )
            return None
        destination = f"{backup_user}@{backup_host}:{backup_path}"

    backups_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = backups_dir / _snapshot_filename()

    # VACUUM INTO cannot run inside a transaction -- SQLite rejects it outright. A fresh
    # AUTOCOMMIT connection sidesteps SQLAlchemy's own autobegin-on-first-statement behavior
    # rather than depending on whatever transaction state a caller-supplied Connection happens to
    # be in.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM INTO ?", (str(snapshot_path),))

    _ensure_destination_exists(
        destination, ssh_key_path=ssh_key_path, known_hosts_path=known_hosts_path
    )
    _rsync(
        str(raw_archive_dir),
        f"{destination}/raw",
        ssh_key_path=ssh_key_path,
        known_hosts_path=known_hosts_path,
    )
    _rsync(
        str(parquet_dir),
        f"{destination}/parquet",
        ssh_key_path=ssh_key_path,
        known_hosts_path=known_hosts_path,
    )
    # The whole backups_dir, not just this run's new snapshot -- rsync only actually transfers
    # what changed, and this is what lets the remote side keep every local-retention-pruned-away
    # snapshot it already received on a prior run.
    _rsync(
        str(backups_dir),
        f"{destination}/db",
        ssh_key_path=ssh_key_path,
        known_hosts_path=known_hosts_path,
    )

    pruned = _prune_local_snapshots(backups_dir, keep=keep_local_snapshots)
    logger.info(
        "backup complete: snapshot=%s destination=%s pruned_local=%d",
        snapshot_path.name,
        destination,
        pruned,
    )
    return BackupResult(
        snapshot_path=snapshot_path, destination=destination, pruned_local_snapshots=pruned
    )


def restore_backup(
    source: str,
    *,
    db_path: Path,
    raw_archive_dir: Path,
    parquet_dir: Path,
    ssh_key_path: str | None = None,
    known_hosts_path: str | None = None,
) -> RestoreResult:
    """Pulls the newest DB snapshot plus the raw/Parquet trees from `source` (symmetric with
    `create_backup`'s `destination` -- a local path or a `user@host:path` remote spec) into the
    given data-dir paths. CLI-only (`sync backup restore`), never exposed via the Settings web UI
    -- see this module's own docstring for why.
    """
    staging = db_path.parent / "_restore_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        staging_db = staging / "db"
        _rsync(
            f"{source}/db",
            str(staging_db),
            ssh_key_path=ssh_key_path,
            known_hosts_path=known_hosts_path,
        )
        snapshots = sorted(staging_db.glob(f"{_SNAPSHOT_PREFIX}*{_SNAPSHOT_SUFFIX}"))
        if not snapshots:
            raise FileNotFoundError(
                f"no {_SNAPSHOT_PREFIX}*{_SNAPSHOT_SUFFIX} snapshot found under {source}/db"
            )
        latest = snapshots[-1]

        raw_archive_dir.mkdir(parents=True, exist_ok=True)
        parquet_dir.mkdir(parents=True, exist_ok=True)
        _rsync(
            f"{source}/raw",
            str(raw_archive_dir),
            ssh_key_path=ssh_key_path,
            known_hosts_path=known_hosts_path,
        )
        _rsync(
            f"{source}/parquet",
            str(parquet_dir),
            ssh_key_path=ssh_key_path,
            known_hosts_path=known_hosts_path,
        )

        shutil.copyfile(latest, db_path)
        logger.info("restore complete: db=%s from snapshot=%s", db_path, latest.name)
        return RestoreResult(restored_db_path=db_path, source_snapshot=latest.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
