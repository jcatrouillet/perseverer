"""Rebuilds the entire database from the raw archive alone — zero network/filesystem access
beyond `raw_object`. This is what proves the raw-first invariant: nothing is lost when the
derived tables are wiped, because parsing is a pure function over the archive.
"""

from pathlib import Path

from sqlalchemy import Connection, delete, select

from perseverer.adapters.garmin_export import report_kind_from_filename
from perseverer.adapters.strava_export import (
    csv_row_from_raw_json,
    ingest_geometry_content,
    ingest_manual_entry_content,
)
from perseverer.archive import read_raw_bytes, restore_raw_object_table
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    activity_stream,
    activity_workout,
    activity_workout_step,
    day_rollup,
    fitness_daily_rollup,
    health_metric_daily_rollup,
    health_metric_period_rollup,
    health_observation,
    health_stream,
    insight,
    lap,
    merge_decision,
    period_rollup,
    raw_object,
    route_geom,
    sleep_session,
    sleep_stage,
)
from perseverer.db.schema import (
    device as device_table,
)
from perseverer.db.schema import (
    split as split_table,
)
from perseverer.fitness import refresh_fitness_rollup
from perseverer.gap import refresh_avg_gap
from perseverer.garmin_activity_summary import backfill_activity_corrections
from perseverer.gpx.parser import parse_gpx
from perseverer.health.eufy_parser import parse_eufy_scale_reading
from perseverer.health.ingest import ingest_health_batch
from perseverer.health.json_parser import (
    parse_daily_hrv_json,
    parse_daily_race_predictions_json,
    parse_daily_sleep_json,
    parse_daily_summary_json,
    parse_daily_training_readiness_json,
    parse_daily_training_status_json,
    parse_garmin_export_json,
    parse_hydration_json,
)
from perseverer.ingest_dispatch import ingest_fit_bytes
from perseverer.insights.engine import refresh_insights
from perseverer.pace_bands import refresh_pace_bands
from perseverer.performance import refresh_vdot
from perseverer.rollups import refresh_daily_and_period_rollups
from perseverer.sport_override import apply_sport_overrides
from perseverer.tcx.parser import parse_tcx
from perseverer.weather_titles import backfill_weather_titles

# Wiped and rebuilt from raw_object. Never includes raw_object itself, and never includes
# metric_definition (a catalog, not per-athlete data — see EXEMPT_FROM_ATHLETE_SCOPING).
# Children before parents so foreign keys are respected regardless of pragma state.
_REBUILDABLE_TABLES = (
    merge_decision,
    # insight.activity_id is nullable but still FK-constrained -- must be cleared before
    # `activity` itself is deleted below. Missing here until now (Phase 8 added `insight`
    # after this table list was last written), which FK-crashed `sync rebuild` for any
    # athlete with insight rows already computed. See ADR 0013.
    insight,
    activity_metric,
    activity_stream,
    activity_workout_step,
    activity_workout,
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
    # strava_export_csv_row raw objects are always archived before the strava_export_gpx/tcx/
    # manual_entry object for the same Activity ID (see adapters/strava_export.py::
    # import_strava_export), so by the time a geometry/manual-entry row is reached below its
    # CSV row is already in this dict -- replay never needs to re-read activities.csv from disk.
    csv_rows_by_activity_id: dict[str, dict[str, str]] = {}
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
        elif row.kind in ("daily_summary_json", "garmin_connect_daily_summary_json"):
            # Same JSON shape, two provenances: fit_folder picks up a dropped
            # daily_summary_*.json file, garmin_connect.py fetches the identical shape live via
            # get_stats() -- both reuse parse_daily_summary_json, no separate branch needed.
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_summary_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "garmin_connect_daily_sleep_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_sleep_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "garmin_connect_daily_hrv_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_hrv_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind in ("hydration_json", "garmin_connect_daily_hydration_json"):
            # Same JSON shape, two provenances -- see the daily_summary_json branch above for
            # the identical reasoning.
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_hydration_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "garmin_connect_daily_training_readiness_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_training_readiness_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "garmin_connect_daily_training_status_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_training_status_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "garmin_connect_race_predictions_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_race_predictions_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "eufy_scale_reading_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_eufy_scale_reading(content),
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
        elif row.kind == "strava_export_csv_row":
            # Remembered for the strava_export_gpx/tcx rows below, which need the same
            # overlay data `_overlay_csv_totals` used at original import time.
            #
            # Also: a manual-entry row (no backing file, ~0.6% of a real archive) has *no*
            # raw_object of its own distinct from this one. `_ingest_manual_entry` archives the
            # exact same `raw_row_json` bytes under kind="strava_export_manual_entry", but
            # `archive_raw_bytes` is idempotent purely on (athlete_id, sha256) -- not kind --
            # so that call always collides with this row's own id and never creates a second,
            # differently-kinded raw_object (confirmed against the real archive: zero
            # strava_export_manual_entry rows exist despite manual-entry activities being
            # present). This *is* the only raw_object such an activity has, so replay must
            # ingest it right here rather than in a separate kind branch. See ADR 0013.
            csv_row = csv_row_from_raw_json(content)
            if row.external_id:
                csv_rows_by_activity_id[row.external_id] = csv_row
            if not csv_row.get("Filename"):
                _created, dates = ingest_manual_entry_content(
                    conn,
                    content,
                    row=csv_row,
                    athlete_id=athlete_id,
                    activity_id=row.external_id or "",
                    raw_id=row.id,
                )
                touched_dates |= dates
            continue
        elif row.kind in ("strava_export_gpx", "strava_export_tcx"):
            # Previously silently dropped here (fell into the catch-all `else: continue`
            # below) -- a real violation of "raw first, must be able to re-derive the entire
            # database from the archive" for every GPX/TCX-sourced Strava activity. See ADR
            # 0013.
            batch = parse_gpx(content) if row.kind == "strava_export_gpx" else parse_tcx(content)
            csv_row = csv_rows_by_activity_id.get(row.external_id or "", {})
            _created, dates = ingest_geometry_content(
                conn,
                parquet_dir,
                content,
                batch=batch,
                row=csv_row,
                athlete_id=athlete_id,
                activity_id=row.external_id or "",
                raw_id=row.id,
            )
            touched_dates |= dates
        else:
            # Forward-compatible: other kinds (garmin_export_json/csv/strava_export_gz/
            # strava_export_other, etc.) are simply skipped on rebuild, not dropped -- their
            # bytes remain archived. strava_export_gz specifically: its decompressed content
            # already has its own separate raw_object (fit/gpx/tcx/other kind) that IS
            # replayed above/via ingest_fit_bytes, so nothing is actually lost.
            continue
        conn.commit()
        replayed += 1

    # Re-applies any athlete-recorded sport corrections (see sport_override.py's own docstring
    # for why this can't just be a mutated `activity.sport` value the replay above writes once
    # and forgets -- the replay just wiped it back to whatever the raw bytes say). Before the
    # rollup/insight refresh below so those see the corrected sport, not the raw one.
    apply_sport_overrides(conn, athlete_id=athlete_id)

    # Re-derives Garmin's own sport/name corrections from summarizedActivitiesExport (see
    # garmin_activity_summary.py's own docstring) -- the replay above just re-parsed every
    # activity from its raw FIT bytes alone, which never carry Garmin Connect's own corrected
    # title (that correction only ever exists in Garmin's cloud record). Missing here until now
    # was a real, confirmed bug: every `sync rebuild` silently reverted every garmin_export
    # activity's name back to its generic FIT-derived default, discarding a correction
    # `import_garmin_export` itself already applies. Before backfill_weather_titles below (same
    # ordering `import_garmin_export` uses) since Garmin's own corrected name can already carry
    # its own weather emoji, and backfill_weather_titles skips a title that already starts with
    # one.
    backfill_activity_corrections(conn, archive_root, athlete_id=athlete_id)

    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    if touched_dates:
        refresh_fitness_rollup(conn, athlete_id=athlete_id)
        refresh_insights(conn, athlete_id=athlete_id)
        refresh_vdot(conn, parquet_dir, athlete_id=athlete_id)
        refresh_pace_bands(conn, parquet_dir, athlete_id=athlete_id)
        refresh_avg_gap(conn, parquet_dir, athlete_id=athlete_id)
        # After apply_sport_overrides above, not before -- that call already restores any
        # previously-set emoji title from the durable override table, so this only ever does
        # real work for an activity that never had one to begin with.
        backfill_weather_titles(conn, archive_root, athlete_id=athlete_id)
    conn.commit()

    return replayed
