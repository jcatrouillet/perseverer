"""Content-addressed raw archive: every byte fetched from any source is stored verbatim,
gzip-compressed, before anything is parsed. See AGENTS.md's "raw first" rule.

`raw_object`'s row in SQLite is itself just a cache of metadata that also lives in a JSON
sidecar next to each blob (`<sha256>.json` alongside `<sha256>.gz`). This is deliberate: the
"DB is fully rebuildable from the archive after deleting it" requirement means the archive on
disk must be self-describing — if raw_object's SQLite rows were the only copy of "what is
this blob, which athlete/source/kind is it," deleting the database would erase the very
catalog rebuild needs to replay. `restore_raw_object_table` reconstructs those rows from the
sidecars alone, with no dependency on SQLite having survived.
"""

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, select

from perseverer.db.schema import raw_object


def _sidecar_path(archive_root: Path, sha256: str) -> Path:
    return archive_root / sha256[:2] / f"{sha256}.json"


def _blob_path(archive_root: Path, sha256: str) -> Path:
    return archive_root / sha256[:2] / f"{sha256}.gz"


def archive_raw_bytes(
    conn: Connection,
    archive_root: Path,
    *,
    athlete_id: str,
    source: str,
    kind: str,
    content: bytes,
    locator: str | None = None,
    external_id: str | None = None,
    http_status: int | None = None,
) -> int:
    """Archive raw bytes content-addressed by sha256, idempotent per (athlete_id, sha256).

    Returns the raw_object.id — either a freshly inserted row or the existing one if this
    exact content was already archived for this athlete. Does not commit; caller controls
    the transaction so archiving and ingest can be one atomic unit.
    """
    sha256 = hashlib.sha256(content).hexdigest()

    existing_id = conn.execute(
        select(raw_object.c.id).where(
            raw_object.c.athlete_id == athlete_id, raw_object.c.sha256 == sha256
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        assert isinstance(existing_id, int)
        return existing_id

    storage_path = f"{sha256[:2]}/{sha256}.gz"
    blob_path = _blob_path(archive_root, sha256)
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(blob_path, "wb") as f:
        f.write(content)

    now = datetime.now(UTC)
    metadata = {
        "athlete_id": athlete_id,
        "source": source,
        "kind": kind,
        "external_id": external_id,
        "source_locator": locator,
        "http_status": http_status,
        "fetched_at": now.isoformat(),
        "sha256": sha256,
        "byte_size": len(content),
        "storage_path": storage_path,
    }
    _sidecar_path(archive_root, sha256).write_text(json.dumps(metadata), encoding="utf-8")

    result = conn.execute(
        raw_object.insert().values(
            athlete_id=athlete_id,
            source=source,
            kind=kind,
            external_id=external_id,
            source_locator=locator,
            http_status=http_status,
            fetched_at=now,
            sha256=sha256,
            byte_size=len(content),
            storage_path=storage_path,
            created_at=now,
        )
    )
    assert result.inserted_primary_key is not None
    inserted_id = result.inserted_primary_key[0]
    assert isinstance(inserted_id, int)
    return inserted_id


def read_raw_bytes(archive_root: Path, storage_path: str) -> bytes:
    with gzip.open(archive_root / storage_path, "rb") as f:
        return f.read()


def restore_raw_object_table(conn: Connection, archive_root: Path, *, athlete_id: str) -> int:
    """Reconstructs raw_object rows for this athlete from the archive's JSON sidecars alone —
    no dependency on any prior SQLite state. Idempotent: existing rows (matched on sha256)
    are left untouched. Returns the number of rows inserted.

    This is what makes "delete the database file entirely, then rebuild" actually work: the
    sidecars are the durable catalog, the SQLite rows are a queryable cache of it.
    """
    existing_hashes = set(
        conn.execute(
            select(raw_object.c.sha256).where(raw_object.c.athlete_id == athlete_id)
        ).scalars()
    )

    inserted = 0
    for sidecar_path in sorted(archive_root.glob("*/*.json")):
        metadata: dict[str, Any] = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if metadata["athlete_id"] != athlete_id or metadata["sha256"] in existing_hashes:
            continue
        conn.execute(
            raw_object.insert().values(
                athlete_id=metadata["athlete_id"],
                source=metadata["source"],
                kind=metadata["kind"],
                external_id=metadata["external_id"],
                source_locator=metadata["source_locator"],
                http_status=metadata["http_status"],
                fetched_at=datetime.fromisoformat(metadata["fetched_at"]),
                sha256=metadata["sha256"],
                byte_size=metadata["byte_size"],
                storage_path=metadata["storage_path"],
                created_at=datetime.fromisoformat(metadata["fetched_at"]),
            )
        )
        existing_hashes.add(metadata["sha256"])
        inserted += 1

    return inserted
