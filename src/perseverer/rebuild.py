"""Rebuilds the entire database from the raw archive alone — zero network/filesystem access
beyond `raw_object`. This is what proves the raw-first invariant: nothing is lost when the
derived tables are wiped, because parsing is a pure function over the archive.
"""

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
from sqlalchemy import Connection, Integer, delete, select

from perseverer.activity_merge import apply_activity_merge_overrides
from perseverer.activity_trim import apply_activity_trim_overrides
from perseverer.adapters.apple_health_export import detect_weight_cutoff_from_eufy
from perseverer.adapters.garmin_export import report_kind_from_filename
from perseverer.adapters.kaya_ingest import (
    KIND_ASCENTS,
    KIND_SESSIONS,
    apply_kaya_sessions,
    store_page,
)
from perseverer.adapters.strava_export import (
    csv_row_from_raw_json,
    ingest_geometry_content,
    ingest_manual_entry_content,
)
from perseverer.archive import read_raw_bytes, restore_raw_object_table
from perseverer.bouldering_overrides import apply_bouldering_route_overrides
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    activity_stream,
    activity_workout,
    activity_workout_step,
    athlete,
    day_rollup,
    fitness_daily_rollup,
    health_metric_daily_rollup,
    health_metric_period_rollup,
    health_observation,
    health_stream,
    ingest_run,
    insight,
    lap,
    merge_decision,
    metadata,
    performance_daily_rollup,
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
from perseverer.health.apple_health_parser import parse_apple_health_export_xml
from perseverer.health.eufy_parser import parse_eufy_scale_reading
from perseverer.health.ingest import ingest_health_batch
from perseverer.health.json_parser import (
    parse_daily_body_battery_json,
    parse_daily_hrv_json,
    parse_daily_race_predictions_json,
    parse_daily_sleep_json,
    parse_daily_stress_json,
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
from perseverer.performance_rollup import refresh_performance_rollup
from perseverer.rollups import refresh_daily_and_period_rollups
from perseverer.running_load import refresh_running_tss
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
    # Missing here would silently mean this table's rows are never wiped/replayed by a shadow
    # rebuild at all -- the exact same class of bug `insight`'s own comment above documents (ADR
    # 0013), caught the same way: `sync rebuild` ran clean (no FK error, since nothing references
    # this table) but left performance_daily_rollup permanently empty on the live side.
    performance_daily_rollup,
)

# The shadow-rebuild swap (rebuild_database_via_shadow) needs the *opposite* traversal from
# _REBUILDABLE_TABLES' own delete order -- parents before children, so a row being inserted
# never references a FK target that doesn't exist yet on the live side. Simply reversing
# _REBUILDABLE_TABLES is *not* sufficient: `device` sits between `activity` and `sleep_stage` in
# the delete order, but `health_observation` (which also references `device_id`) sits further
# along still, so a naive reversal would insert health_observation before device ever lands.
# This list is the real topological order for the 21 tables' actual FK graph (confirmed against
# db/schema.py directly, not assumed): device/sleep_session/health_stream/the five rollup tables
# have no dependency on anything else in this set and can go first in any order; sleep_stage
# needs sleep_session; activity needs device; everything else needs activity. A test
# (test_rebuild.py) asserts this covers the exact same 21 tables as _REBUILDABLE_TABLES, so the
# two lists can't silently drift apart if a future table is added to one and not the other.
_SHADOW_SWAP_INSERT_ORDER = (
    device_table,
    sleep_session,
    health_stream,
    day_rollup,
    period_rollup,
    health_metric_daily_rollup,
    health_metric_period_rollup,
    fitness_daily_rollup,
    performance_daily_rollup,
    sleep_stage,
    activity,
    health_observation,
    activity_source_link,
    activity_metric,
    activity_stream,
    activity_workout_step,
    activity_workout,
    lap,
    split_table,
    route_geom,
    insight,
    merge_decision,
)

# Five FK columns, across four tables, can't have their value copied straight from the shadow
# database: each references another table's own surrogate `id`, and that id is either freshly
# reassigned during this same swap (device, sleep_session -- see _swap_tables_from_shadow's own
# docstring for why every surrogate id is reassigned rather than copied) or was never touched by
# the swap at all (raw_object, which isn't one of the 21 rebuildable tables -- it's preserved,
# populated identically in both databases from the same on-disk archive sidecars). Each entry
# maps (table, column) to (the extra JOIN clauses needed, the SELECT expression to use instead
# of a plain column copy), resolving the referenced row through its own natural/business key --
# device's (athlete_id, manufacturer, product, serial_number), sleep_session's (athlete_id,
# local_date, source), raw_object's (athlete_id, sha256) -- rather than its now-meaningless
# shadow-side numeric id.
_FK_REMAP_JOINS: dict[tuple[str, str], tuple[str, str]] = {
    ("activity", "device_id"): (
        'LEFT JOIN shadow_db.device AS shadow_device ON shadow_device.id = src.device_id '
        "LEFT JOIN device AS live_device ON live_device.athlete_id = shadow_device.athlete_id "
        "AND live_device.manufacturer = shadow_device.manufacturer "
        "AND live_device.product = shadow_device.product "
        "AND live_device.serial_number = shadow_device.serial_number",
        "live_device.id",
    ),
    ("health_observation", "device_id"): (
        'LEFT JOIN shadow_db.device AS shadow_ho_device ON shadow_ho_device.id = src.device_id '
        "LEFT JOIN device AS live_ho_device "
        "ON live_ho_device.athlete_id = shadow_ho_device.athlete_id "
        "AND live_ho_device.manufacturer = shadow_ho_device.manufacturer "
        "AND live_ho_device.product = shadow_ho_device.product "
        "AND live_ho_device.serial_number = shadow_ho_device.serial_number",
        "live_ho_device.id",
    ),
    ("health_observation", "raw_object_id"): (
        "LEFT JOIN shadow_db.raw_object AS shadow_ho_raw ON shadow_ho_raw.id = src.raw_object_id "
        "LEFT JOIN raw_object AS live_ho_raw ON live_ho_raw.athlete_id = shadow_ho_raw.athlete_id "
        "AND live_ho_raw.sha256 = shadow_ho_raw.sha256",
        "live_ho_raw.id",
    ),
    ("sleep_session", "raw_object_id"): (
        "LEFT JOIN shadow_db.raw_object AS shadow_ss_raw ON shadow_ss_raw.id = src.raw_object_id "
        "LEFT JOIN raw_object AS live_ss_raw ON live_ss_raw.athlete_id = shadow_ss_raw.athlete_id "
        "AND live_ss_raw.sha256 = shadow_ss_raw.sha256",
        "live_ss_raw.id",
    ),
    ("sleep_stage", "sleep_session_id"): (
        "JOIN shadow_db.sleep_session AS shadow_sess ON shadow_sess.id = src.sleep_session_id "
        "JOIN sleep_session AS live_sess ON live_sess.athlete_id = shadow_sess.athlete_id "
        "AND live_sess.local_date = shadow_sess.local_date "
        "AND live_sess.source = shadow_sess.source",
        "live_sess.id",
    ),
    ("activity_source_link", "raw_object_id"): (
        "JOIN shadow_db.raw_object AS shadow_asl_raw ON shadow_asl_raw.id = src.raw_object_id "
        "JOIN raw_object AS live_asl_raw ON live_asl_raw.athlete_id = shadow_asl_raw.athlete_id "
        "AND live_asl_raw.sha256 = shadow_asl_raw.sha256",
        "live_asl_raw.id",
    ),
}


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
        # id as a tiebreaker -- two raw objects sharing an identical archived fetched_at
        # timestamp would otherwise have DB-unspecified relative order, and replay order feeds
        # directly into merge-matching (_find_merge_match's "first source wins" identity
        # anchor), which must reproduce the exact same result on every rebuild.
        .order_by(raw_object.c.fetched_at.asc(), raw_object.c.id.asc())
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
        elif row.kind == "garmin_connect_daily_body_battery_json":
            # Superseded as the live fetch source (see fetch_and_ingest_daily_body_battery's own
            # docstring) -- this branch stays so already-archived raw bytes of this older shape
            # still replay (raw-first/never-destructive).
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_body_battery_json(content),
            )
            touched_dates |= health_result.affected_local_dates
        elif row.kind == "garmin_connect_daily_stress_json":
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_daily_stress_json(content),
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
        elif row.kind == "apple_health_export_xml":
            # weight_before isn't stored on raw_object -- re-derived here exactly like
            # import_apple_health_export's own CLI layer does, rather than persisting extra
            # metadata (matching this file's own report_kind_from_filename precedent just above).
            # Safe because eufy_scale_reading_json rows replay earlier in fetched_at order than
            # this one ever could (Eufy predates any Apple Health import by construction), so
            # Eufy's data is already in health_observation by the time this branch runs.
            weight_before = detect_weight_cutoff_from_eufy(conn, athlete_id)
            health_result = ingest_health_batch(
                conn,
                parquet_dir,
                athlete_id=athlete_id,
                source=row.source,
                batch=parse_apple_health_export_xml(content, weight_before=weight_before),
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
        elif row.kind in (KIND_SESSIONS, KIND_ASCENTS):
            # Durable kaya_session/kaya_ascent tables are re-upserted from the archived page
            # here; the activities/splits derived from them are rebuilt by apply_kaya_sessions
            # after the loop (see ADR 0016).
            store_page(
                conn,
                athlete_id=athlete_id,
                raw_object_id=row.id,
                kind=row.kind,
                content=content,
            )
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

    # Every step from here through the end of this function commits on its own rather than
    # sharing one long transaction across all of them -- confirmed necessary live: a real
    # rebuild on bercy once put this entire tail into a single uncommitted transaction that hung
    # partway through (inside a live multi-worker process, most likely a lock/resource
    # contention issue between this long-running writer and the app's own concurrent request
    # handling) and had to be recovered by manually re-running just these calls standalone.
    # Committing after each call bounds how much work a future hang or crash here can lose, and
    # -- as the recovery itself demonstrated -- lets exactly this tail be safely re-run on its
    # own against already-replayed data, without redoing the (far more expensive) per-row replay
    # loop above.

    # Re-applies any athlete-recorded sport corrections (see sport_override.py's own docstring
    # for why this can't just be a mutated `activity.sport` value the replay above writes once
    # and forgets -- the replay just wiped it back to whatever the raw bytes say). Before the
    # rollup/insight refresh below so those see the corrected sport, not the raw one.
    apply_sport_overrides(conn, athlete_id=athlete_id)
    conn.commit()

    # Same "durable, never-wiped correction re-applied after every rebuild" shape as
    # apply_sport_overrides above, for the athlete's own bouldering route-status corrections and
    # manually-added routes -- see bouldering_overrides.py's own docstring.
    apply_bouldering_route_overrides(conn, athlete_id=athlete_id)
    conn.commit()

    # After the overrides above, not before: for a day Kaya covers, its route list replaces the
    # Garmin-decoded splits those overrides were keyed to (ADR 0016), so Kaya must win.
    touched_dates |= apply_kaya_sessions(conn, athlete_id=athlete_id)
    conn.commit()

    # Same durable-correction shape again, for the athlete's own car-travel trims -- see
    # activity_trim.py's own docstring. No SQLite attachment needed (unlike the API's shared
    # duckdb connection), just enough to read_parquet() the activity's own stream, so a bare
    # in-memory connection is created here rather than threading api/duckdb_conn.py's
    # heavier setup through the CLI rebuild path.
    apply_activity_trim_overrides(
        conn, duckdb.connect(":memory:"), parquet_dir, athlete_id=athlete_id
    )
    conn.commit()

    # Same durable-correction shape again, for the athlete's own manual "these two activities
    # are the same" merges -- see activity_merge.py's own docstring. Natural merge-matching
    # (_find_merge_match, run for each activity during the replay above) may have already
    # caught some of these on its own this time (e.g. the hike/walk merge-family leniency now
    # catches cases it didn't before) -- apply_activity_merge_overrides skips a durable row once
    # both its anchors already resolve to the same activity_id, so this is a no-op for those.
    apply_activity_merge_overrides(conn, athlete_id=athlete_id)
    conn.commit()

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
    conn.commit()

    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    conn.commit()
    if touched_dates:
        refresh_vdot(conn, parquet_dir, athlete_id=athlete_id)
        conn.commit()
        refresh_pace_bands(conn, parquet_dir, athlete_id=athlete_id)
        conn.commit()
        refresh_avg_gap(conn, parquet_dir, athlete_id=athlete_id)
        conn.commit()
        # After refresh_vdot/refresh_avg_gap, not before -- both feed its rolling-VDOT/
        # threshold-HR-candidate inputs.
        refresh_performance_rollup(conn, athlete_id=athlete_id)
        conn.commit()
        # After refresh_avg_gap, not before -- running_tss's rTSS formula consumes the
        # grade-adjusted speed refresh_avg_gap just wrote.
        refresh_running_tss(conn, athlete_id=athlete_id)
        conn.commit()
        # After refresh_running_tss, not before -- fitness.py's CTL/ATL input now prefers
        # running_tss's pace-calibrated value over training_load_peak wherever one exists.
        refresh_fitness_rollup(conn, athlete_id=athlete_id)
        conn.commit()
        refresh_insights(conn, athlete_id=athlete_id)
        conn.commit()
        # After apply_sport_overrides above, not before -- that call already restores any
        # previously-set emoji title from the durable override table, so this only ever does
        # real work for an activity that never had one to begin with.
        backfill_weather_titles(conn, archive_root, athlete_id=athlete_id)
        conn.commit()

    return replayed


def _insert_from_shadow_sql(table: object) -> str:
    """Builds one table's INSERT...SELECT statement for the shadow-to-live swap. Never copies an
    *integer* column named `id`: on every table where that's the primary key, it's an
    autoincrement surrogate assigned independently by its own database's own sequence, so the
    same integer can already be in use by a different, unrelated live row -- confirmed the hard
    way (a real UNIQUE constraint collision on day_rollup.id, the first time this ran against a
    live table that already had another athlete's data in it). Left to autoincrement fresh here
    instead. `activity` is deliberately excluded from this exclusion: its own `id` column is
    also named `id`, but it's a content-derived *string*, stable and identical between shadow
    and live by construction, not a per-database sequence value -- confirmed the hard way too
    (a blanket "skip any column named id" first cut wrongly dropped it, immediately surfacing as
    a `NOT NULL constraint failed: activity.id` on the very next insert). A handful of other
    columns, listed in _FK_REMAP_JOINS, resolve through the referenced row's own natural key
    instead of a straight copy, for the same reason autoincrement ids don't copy safely."""
    all_cols: list[str] = list(table.c.keys())  # type: ignore[attr-defined]
    insert_cols = [
        c for c in all_cols if not (c == "id" and isinstance(table.c[c].type, Integer))  # type: ignore[attr-defined]
    ]
    joins: list[str] = []
    select_exprs: list[str] = []
    for col in insert_cols:
        remap = _FK_REMAP_JOINS.get((table.name, col))  # type: ignore[attr-defined]
        if remap is None:
            select_exprs.append(f"src.{col}")
        else:
            join_sql, select_expr = remap
            joins.append(join_sql)
            select_exprs.append(select_expr)
    cols_sql = ", ".join(insert_cols)
    select_sql = ", ".join(select_exprs)
    join_sql = " ".join(joins)
    return (
        f'INSERT INTO "{table.name}" ({cols_sql}) '  # type: ignore[attr-defined]
        f'SELECT {select_sql} FROM shadow_db."{table.name}" AS src '  # type: ignore[attr-defined]
        f"{join_sql} WHERE src.athlete_id = ?"
    )


def _swap_tables_from_shadow(live_db_path: Path, shadow_db_path: Path, *, athlete_id: str) -> None:
    """The one moment a shadow rebuild ever touches the live tables: a single transaction that
    deletes this athlete's rows from every rebuildable table and replaces them with the shadow
    database's freshly-replayed rows for the same athlete. Deletes in `_REBUILDABLE_TABLES`' own
    children-before-parents order (same order the in-place wipe already uses); inserts in
    `_SHADOW_SWAP_INSERT_ORDER` (the real topological order for this FK graph -- see that
    tuple's own comment for why simply reversing the delete order isn't sufficient). Column
    lists are enumerated explicitly on both sides rather than `SELECT *`, so a live/shadow
    column-order mismatch can never silently corrupt data -- see `_insert_from_shadow_sql`.

    Opens its own brand-new `sqlite3.connect(live_db_path)` rather than reusing the SQLAlchemy
    `Connection` the rest of this module threads around -- confirmed necessary the hard way:
    issuing ATTACH/DETACH through `Connection.connection.dbapi_connection` (SQLAlchemy's own
    pooled DBAPI handle) intermittently left SQLite unable to DETACH afterwards ("database
    shadow_db is locked"), for reasons that didn't reduce to any single cause worth chasing
    further -- a fully independent connection, opened and closed only for this one swap, sidesteps
    whatever residual pooled-connection state was responsible. This is safe: SQLite's WAL mode is
    explicitly designed for multiple concurrent connections to the same file (verified directly,
    not assumed, before this function was written -- see the module-level note on the empirical
    atomicity check), so a second connection existing alongside the caller's own `conn` for the
    handful of statements this needs is exactly the supported case, not a workaround.
    """
    # True autocommit at the Python driver level (isolation_level=None), explicit BEGIN/COMMIT
    # SQL below, and `cached_statements=0` -- confirmed empirically (not assumed) that DETACH
    # otherwise fails with "database shadow_db is locked" once this loop issues around 20+
    # distinct SQL texts against the attach: Python's sqlite3 module caches prepared statements
    # per connection (default up to 128), and a cached statement referencing shadow_db counts,
    # from SQLite's perspective, as the database still being in use, so DETACH refuses until
    # every such statement is finalized. Disabling the cache finalizes each statement right
    # after it runs instead of holding it for reuse -- irrelevant here anyway, since none of
    # these 21x2 statements repeat within a single call.
    raw = sqlite3.connect(live_db_path, isolation_level=None, cached_statements=0)
    try:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute(f"ATTACH DATABASE '{shadow_db_path.as_posix()}' AS shadow_db")
        try:
            raw.execute("BEGIN")
            # metric_definition is a shared, growing catalog -- never wiped/replayed (not one of
            # the 21 rebuildable tables), but the shadow's own replay may have registered a new
            # metric key nothing on the live side has ever seen yet (confirmed the hard way: a
            # real FOREIGN KEY failure on activity_metric.metric_key without this). Merged in
            # additively, matching this project's own "additive schema evolution" principle --
            # never overwrites a live row, only adds ones missing (keyed by metric_key itself, a
            # natural key, not a per-database surrogate id, so there's nothing to remap here).
            raw.execute(
                "INSERT OR IGNORE INTO metric_definition SELECT * FROM shadow_db.metric_definition"
            )
            for table in _REBUILDABLE_TABLES:
                raw.execute(f'DELETE FROM "{table.name}" WHERE athlete_id = ?', (athlete_id,))
            for table in _SHADOW_SWAP_INSERT_ORDER:
                raw.execute(_insert_from_shadow_sql(table), (athlete_id,))
            raw.execute("COMMIT")
        finally:
            raw.execute("DETACH DATABASE shadow_db")
    finally:
        raw.close()


def _swap_parquet_from_shadow(shadow_parquet_dir: Path, parquet_dir: Path) -> None:
    """Per-file atomic replace of every file the shadow rebuild wrote, mirroring
    `_swap_tables_from_shadow`'s all-or-nothing intent as closely as a filesystem allows: each
    individual `os.replace` is atomic (a concurrent DuckDB `read_parquet()` -- stream_query.py,
    activity_trim.py -- never sees a half-written file), even though the whole directory tree
    isn't swapped in one atomic step the way the SQL side is. Only ever called after
    `_swap_tables_from_shadow` has already committed. Walks every file already isolated under
    the athlete's own parquet subtree (streams.py::write_activity_stream/write_health_stream
    both write under `<athlete_id>/...`), so this only ever touches that one athlete's files."""
    for shadow_file in shadow_parquet_dir.rglob("*"):
        if not shadow_file.is_file():
            continue
        real_file = parquet_dir / shadow_file.relative_to(shadow_parquet_dir)
        real_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(shadow_file), str(real_file))


def rebuild_database_via_shadow(
    conn: Connection, archive_root: Path, parquet_dir: Path, data_dir: Path, *, athlete_id: str
) -> int:
    """Same end result as `rebuild_database`, but the live tables are never wiped in place --
    every raw object is replayed into a throwaway shadow database first, and only once that
    replay succeeds does a short, atomic swap (`_swap_tables_from_shadow`) replace the live
    athlete's rows with the shadow's. If the replay raises or is killed at any point, the live
    tables were never opened for writing at all: nothing to roll back, no user-facing impact.

    This exists because a real rebuild on bercy once hung for 5+ hours *after* wiping the live
    tables, leaving the whole app reading broken/incomplete data for the entire outage (and,
    separately, an interrupted rebuild during this same investigation left a local dev database
    in the identical state) -- see rebuild.py's own module history / the ADR for the incident.
    The replay itself keeps its full existing risk profile (still slow, still capable of
    hanging) -- what changes is that none of that is ever visible to anything reading the live
    database, because the live tables are never touched until the replay has already fully
    succeeded.
    """
    shadow_dir = data_dir / "tmp" / f"rebuild_shadow_{uuid4().hex}"
    shadow_db_path = shadow_dir / "shadow.db"
    shadow_parquet_dir = shadow_dir / "parquet"
    shadow_parquet_dir.mkdir(parents=True, exist_ok=True)

    live_db_path = Path(str(conn.engine.url.database))

    # `raw_object` isn't one of the 21 rebuildable tables -- in real production use it's already
    # populated on the live side from ordinary ingestion (raw-first: every vendor fetch archives
    # here regardless of any rebuild), long before a rebuild is ever triggered. But nothing
    # actually guarantees that at this point (confirmed the hard way: a rebuild against a
    # database that had never been touched otherwise left `activity_source_link`'s raw_object_id
    # remap with nothing to match, silently dropping every row via its INNER JOIN), so this is
    # called here defensively too. Already idempotent (matches on sha256, see its own docstring)
    # -- a no-op in the normal case where the live raw_object table is already current.
    restore_raw_object_table(conn, archive_root, athlete_id=athlete_id)
    conn.commit()

    shadow_engine = make_engine(shadow_db_path)
    try:
        metadata.create_all(shadow_engine)
        with shadow_engine.connect() as shadow_conn:
            # The shadow db needs its own `athlete` row before any FK-constrained insert can
            # succeed -- metric_definition, by contrast, self-populates as ingestion calls
            # get_or_register_metric, exactly like a real fresh install does.
            athlete_row = conn.execute(
                select(athlete).where(athlete.c.id == athlete_id)
            ).mappings().one()
            # Releases this read's own implicit transaction/snapshot on `conn` immediately,
            # rather than holding it open for however long the replay below takes -- otherwise
            # `conn`'s next query after this function returns would still see the pre-swap state
            # (correct per WAL snapshot isolation, but not what any caller here wants).
            conn.commit()
            shadow_conn.execute(athlete.insert().values(dict(athlete_row)))
            shadow_conn.commit()

            replayed = rebuild_database(
                shadow_conn, archive_root, shadow_parquet_dir, athlete_id=athlete_id
            )

        shadow_engine.dispose()
        _swap_tables_from_shadow(live_db_path, shadow_db_path, athlete_id=athlete_id)
        _swap_parquet_from_shadow(shadow_parquet_dir, parquet_dir)
        return replayed
    finally:
        shadow_engine.dispose()
        shutil.rmtree(shadow_dir, ignore_errors=True)


def rebuild_database_tracked(
    conn: Connection, archive_root: Path, parquet_dir: Path, data_dir: Path, *, athlete_id: str
) -> int:
    """Same as rebuild_database_via_shadow above, wrapped with an `ingest_run` row
    (source="rebuild") so an API-triggered rebuild (api/routers/settings.py) can be polled for
    status the same way sync_garmin_connect's own run already can — mirrors that function's own
    try/except-and-record pattern (adapters/garmin_connect.py). `sync rebuild` (cli.py) uses this
    same shadow path too now (not the plain in-place `rebuild_database`) — a rebuild run directly
    on a box where perseverer-api/perseverer-worker are already live (bercy, always) hits the
    exact same "live tables wiped mid-replay" risk this function's shadow approach exists to
    avoid, whether it's triggered from the CLI or the Settings page.
    """
    started_at = datetime.now(UTC)
    result = conn.execute(
        ingest_run.insert().values(
            athlete_id=athlete_id,
            source="rebuild",
            started_at=started_at,
            status="running",
            items_seen=0,
            items_new=0,
        )
    )
    assert result.inserted_primary_key is not None
    run_id = result.inserted_primary_key[0]
    assert isinstance(run_id, int)
    conn.commit()

    try:
        replayed = rebuild_database_via_shadow(
            conn, archive_root, parquet_dir, data_dir, athlete_id=athlete_id
        )
    except Exception as e:
        conn.execute(
            ingest_run.update()
            .where(ingest_run.c.id == run_id)
            .values(
                finished_at=datetime.now(UTC),
                status="failed",
                errors=json.dumps([{"error": str(e)}]),
            )
        )
        conn.commit()
        raise

    conn.execute(
        ingest_run.update()
        .where(ingest_run.c.id == run_id)
        .values(
            finished_at=datetime.now(UTC),
            status="success",
            items_seen=replayed,
            items_new=replayed,
        )
    )
    conn.commit()
    return replayed
