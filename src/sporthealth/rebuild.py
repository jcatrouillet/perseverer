"""Rebuilds the entire database from the raw archive alone — zero network/filesystem access
beyond `raw_object`. This is what proves the raw-first invariant: nothing is lost when the
derived tables are wiped, because parsing is a pure function over the archive.
"""

from pathlib import Path

from sqlalchemy import Connection, delete, select

from sporthealth.adapters.garmin_export import report_kind_from_filename
from sporthealth.archive import read_raw_bytes, restore_raw_object_table
from sporthealth.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    activity_stream,
    day_rollup,
    fitness_daily_rollup,
    health_metric_daily_rollup,
    health_metric_period_rollup,
    health_observation,
    health_stream,
    lap,
    merge_decision,
    period_rollup,
    raw_object,
    route_geom,
    sleep_session,
    sleep_stage,
)
from sporthealth.db.schema import (
    device as device_table,
)
from sporthealth.db.schema import (
    split as split_table,
)
from sporthealth.fitness import refresh_fitness_rollup
from sporthealth.health.ingest import ingest_health_batch
from sporthealth.health.json_parser import (
    parse_daily_summary_json,
    parse_garmin_export_json,
    parse_hydration_json,
)
from sporthealth.ingest_dispatch import ingest_fit_bytes
from sporthealth.rollups import refresh_daily_and_period_rollups

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
    sleep_stage,
    health_observation,
    health_stream,
    sleep_session,
    health_metric_period_rollup,
    period_rollup,
    health_metric_daily_rollup,
    day_rollup,
    fitness_daily_rollup,
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
            raw_object.c.external_id,
            raw_object.c.source_locator,
        )
        .where(raw_object.c.athlete_id == athlete_id)
        .order_by(raw_object.c.fetched_at)
    ).fetchall()

    replayed = 0
    touched_dates: set[str] = set()
    for row in rows:
        content = read_raw_bytes(archive_root, row.storage_path)

        # "fit" covers Phase 2's unified dispatch (activity-or-health); "fit_activity" is the
        # Phase 1 kind, kept for backward compatibility with rows archived before this change.
        # archive_raw_bytes is content-addressed and idempotent, so replaying through the same
        # dispatch used at ingest time re-finds this exact raw_object.id rather than duplicating
        # it.
        if row.kind.startswith("fit"):
            dispatch_result = ingest_fit_bytes(
                conn,
                archive_root,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                content=content,
                external_id_hint=row.external_id,
            )
            touched_dates |= dispatch_result.affected_local_dates()
        elif row.kind == "daily_summary_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_summary_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "hydration_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_hydration_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "garmin_export_health_json":
            # report_kind isn't stored as its own column -- re-derive it from source_locator
            # (the original export filename), the same way garmin_export.py derives it at
            # ingest time. Phase 6: this branch was missing entirely until now, meaning
            # `sync rebuild` silently dropped every garmin.export.* health observation (see
            # docs/adr/0009-phase-6-calendar-rollups-fitness-health.md).
            report_kind = report_kind_from_filename(Path(row.source_locator or "").name)
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_garmin_export_json(content, report_kind=report_kind),
            )
            touched_dates |= health_result.affected_local_dates
        else:
            # Forward-compatible: other kinds (garmin_export_json/csv/other, etc.) are
            # simply skipped on rebuild, not dropped — their bytes remain archived.
            continue
        conn.commit()
        replayed += 1

    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    if touched_dates:
        refresh_fitness_rollup(conn, athlete_id=athlete_id)
    conn.commit()

    return replayed
