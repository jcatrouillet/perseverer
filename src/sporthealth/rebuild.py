"""Rebuilds the entire database from the raw archive alone — zero network/filesystem access
beyond `raw_object`. This is what proves the raw-first invariant: nothing is lost when the
derived tables are wiped, because parsing is a pure function over the archive.
"""

from pathlib import Path

from sqlalchemy import Connection, delete, select

from sporthealth.adapters.fit_folder import ingest_canonical_batch
from sporthealth.archive import read_raw_bytes, restore_raw_object_table
from sporthealth.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    activity_stream,
    lap,
    merge_decision,
    raw_object,
    route_geom,
)
from sporthealth.db.schema import (
    device as device_table,
)
from sporthealth.db.schema import (
    split as split_table,
)
from sporthealth.fit.parser import parse_fit

# Wiped and rebuilt from raw_object. Never includes raw_object itself, and never includes
# metric_definition (a catalog, not per-athlete data — see EXEMPT_FROM_ATHLETE_SCOPING).
# Children before parents so foreign keys are respected regardless of pragma state.
_REBUILDABLE_TABLES = (
    merge_decision,
    activity_metric,
    activity_stream,
    lap,
    split_table,
    route_geom,
    activity_source_link,
    activity,
    device_table,
)


def rebuild_database(
    conn: Connection, archive_root: Path, parquet_dir: Path, *, athlete_id: str
) -> int:
    """Wipes all rebuildable tables for this athlete and replays every raw_object through
    parse + ingest, in original fetch order. Returns the number of raw objects replayed.

    Restores raw_object itself from the archive's JSON sidecars first — this is what makes
    rebuilding after deleting the *entire* database file (not just the derived tables) work,
    since raw_object's SQLite rows would otherwise be gone too. See archive.py.
    """
    restore_raw_object_table(conn, archive_root, athlete_id=athlete_id)
    conn.commit()

    for table in _REBUILDABLE_TABLES:
        conn.execute(delete(table).where(table.c.athlete_id == athlete_id))
    conn.commit()

    rows = conn.execute(
        select(
            raw_object.c.id,
            raw_object.c.kind,
            raw_object.c.source,
            raw_object.c.storage_path,
            raw_object.c.sha256,
        )
        .where(raw_object.c.athlete_id == athlete_id)
        .order_by(raw_object.c.fetched_at)
    ).fetchall()

    replayed = 0
    for row in rows:
        if not row.kind.startswith("fit_"):
            # Forward-compatible: only FIT kinds are parsed/handled as of Phase 1. Other
            # kinds are simply skipped on rebuild, not dropped — their bytes remain archived.
            continue
        content = read_raw_bytes(archive_root, row.storage_path)
        batch = parse_fit(content)
        ingest_canonical_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=row.source,
            raw_object_id=row.id,
            sha256=row.sha256,
            batch=batch,
        )
        conn.commit()
        replayed += 1

    return replayed
