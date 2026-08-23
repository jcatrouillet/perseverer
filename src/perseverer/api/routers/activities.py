"""GET /activities, GET /activities/{id}, GET /activities/{id}/stream."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import Connection, Engine, Row, case, func, select

from perseverer.activity_trim import clear_activity_trim, set_activity_trim
from perseverer.adapters.fit_folder import _local_date, _upsert_device, insert_new_activity
from perseverer.adapters.strava_export import _overlay_csv_totals
from perseverer.api.dependencies import get_conn, get_duckdb, get_engine, require_api_key
from perseverer.api.schemas.activities import (
    ActivityComparisonRowOut,
    ActivityComparisonsOut,
    ActivityContextOut,
    ActivityContextRecentOut,
    ActivityDetail,
    ActivityFuelingIn,
    ActivityFuelingOut,
    ActivityLocationOut,
    ActivityMapPointOut,
    ActivityMergeDecisionOut,
    ActivityMetricOut,
    ActivityNameOverrideIn,
    ActivityNameOverrideOut,
    ActivityRaceOverrideIn,
    ActivityRaceOverrideOut,
    ActivityRouteOut,
    ActivitySourceOut,
    ActivitySourcesOut,
    ActivitySplitOut,
    ActivitySportOverrideIn,
    ActivitySportOverrideOut,
    ActivitySummary,
    ActivityTrimIn,
    ActivityWeatherOut,
    ActivityWorkoutOut,
    ActivityWorkoutStepOut,
    ClimbComparisonRowOut,
    ClimbComparisonsOut,
    ClimbGradeBreakdownOut,
    ClimbingSummaryOut,
    ClimbRouteAddIn,
    ClimbRouteStatusIn,
    DeviceOut,
    LapOut,
    RouteOut,
    SplitOut,
    TransportMixFlagOut,
)
from perseverer.api.schemas.common import Page, to_utc
from perseverer.api.schemas.insights import InsightOut
from perseverer.api.schemas.streams import StreamResponse
from perseverer.archive import read_raw_bytes
from perseverer.bouldering_overrides import (
    add_manual_route,
    delete_manual_route,
    set_route_status_override,
)
from perseverer.config import Settings, get_settings
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    activity_stream,
    activity_trim_override,
    activity_workout,
    activity_workout_step,
    health_observation,
    lap,
    merge_decision,
    raw_object,
    route_geom,
)
from perseverer.db.schema import device as device_table
from perseverer.db.schema import split as split_table
from perseverer.fitness import refresh_fitness_rollup
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.geocoding import get_or_fetch_activity_location, read_cached_location
from perseverer.insights.engine import load_insight_activities, refresh_insights
from perseverer.insights.rules_activity import compute_activity_insights
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.reparse import reparse_raw_object
from perseverer.rollups import refresh_daily_and_period_rollups
from perseverer.sport_override import (
    set_fueling_override,
    set_name_override,
    set_race_override,
    set_sport_override,
)
from perseverer.stream_query import downsample
from perseverer.transport_mix import ELIGIBLE_SPORTS, detect_transport_mix
from perseverer.weather import get_or_fetch_activity_weather

router = APIRouter()

# Avg/max heart rate lives under one of two metric_key namespaces depending on source: FIT's own
# session field for fit_folder/garmin_export/garmin_connect, or activities.csv's own Average/Max
# Heart Rate columns (strava_export.py's CSV-totals overlay) for GPX/TCX-sourced Strava
# activities, which have no FIT session message to read a value from at all. Same alias-merge
# shape as api/routers/health.py::LOGICAL_METRICS -- see ADR 0013.
AVG_HR_METRIC_KEYS = ("fit.session.avg_heart_rate", "strava.session.avg_heart_rate")
MAX_HR_METRIC_KEYS = ("fit.session.max_heart_rate", "strava.session.max_heart_rate")

# A single FIT-only key, no strava.* alias -- unlike heart rate, GPX/TCX-sourced Strava
# activities carry no cadence field for the CSV-totals overlay to fill in at all, so there's
# nothing to alias against. Same key insights/engine.py already uses for the identical field.
# Its value is a single-foot rate; doubled to strides/min at the point of use below, matching
# frontend/src/components/ActivityStatsGrid.tsx's own convention (confirmed against real data:
# session values of ~75-88 correspond to the conventional 150-176 spm runners actually see).
CADENCE_METRIC_KEY = "fit.session.avg_running_cadence"


def _aliased_metric_subquery(keys: tuple[str, ...]):  # type: ignore[no-untyped-def]
    """A correlated scalar subquery preferring the first key in `keys` that has a value for
    this activity -- `keys` is ordered by priority, not just membership."""
    priority = case(*[(activity_metric.c.metric_key == k, i) for i, k in enumerate(keys)])
    return (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key.in_(keys),
        )
        .order_by(priority)
        .limit(1)
        .scalar_subquery()
    )


def _first_metric(metrics_by_key: dict[str, float], keys: tuple[str, ...]) -> float | None:
    """Same alias-merge as `_aliased_metric_subquery`, for callers (get_activity) that already
    have the full per-activity metrics dict in hand rather than issuing a new subquery."""
    for key in keys:
        if key in metrics_by_key:
            return metrics_by_key[key]
    return None


# fit.session.workout_rpe is an unrecognized/generic field (see fit/parser.py's docstring), so
# nothing descales it on ingest -- its stored value_num is the raw FIT uint8, Borg CR10 x10 per
# the installed garmin_fit_sdk's profile.py (field 193: "Common Borg CR10 / 0-10 RPE scale,
# multiplied 10x"). One helper so list_activities and get_activity apply the exact same
# conversion rather than each hand-rolling `/ 10`.
def _workout_rpe_from_raw(raw: float | None) -> float | None:
    return raw / 10 if raw is not None else None


# Garmin's estimated sweat loss isn't a FIT session field at all -- it's logged to the athlete's
# daily hydration log (`garmin.export.HydrationLogFile.estimatedSweatLossInML`, a
# `health_observation`, not an `activity_metric`) with no activity_id of its own. Verified
# directly against the real archive before building this (per CLAUDE.md's verify-don't-assume
# rule): every one of 13 real activities spanning a week each had exactly one hydration-log entry
# landing within about a minute after the activity's own end time (`start_time_utc + duration_s`)
# -- e.g. an activity ending at 18:26:00 paired with a log entry at 18:26:53. The 30-minute window
# below is deliberately generous relative to that ~1-minute real-world gap, to tolerate sync
# delay, without risking a false match to an unrelated later activity's own hydration entry.
_SWEAT_LOSS_METRIC_KEY = "garmin.export.HydrationLogFile.estimatedSweatLossInML"
_SWEAT_LOSS_MATCH_WINDOW_S = 1800


def _estimated_sweat_loss_ml(
    conn: Connection, athlete_id: str, activity_end_utc: datetime
) -> float | None:
    row = conn.execute(
        select(health_observation.c.value_num)
        .where(
            health_observation.c.athlete_id == athlete_id,
            health_observation.c.metric_key == _SWEAT_LOSS_METRIC_KEY,
            health_observation.c.observed_at_utc >= activity_end_utc,
            health_observation.c.observed_at_utc
            <= activity_end_utc + timedelta(seconds=_SWEAT_LOSS_MATCH_WINDOW_S),
        )
        .order_by(health_observation.c.observed_at_utc.asc())
        .limit(1)
    ).fetchone()
    return row.value_num if row is not None else None


def _climb_summary_from_splits(
    splits: Sequence[Row[Any]],
) -> tuple[int | None, int | None, float | None]:
    """Bouldering-only summary derived from an already-fetched list of split rows -- used by
    `get_activity`, which already has the full `splits` list in hand, so there's no reason to
    issue a second query the way `list_activities`' own three subqueries (§ below) have to.
    Returns (route_count, max_completed_grade, climb_time_s), all None when this activity has no
    climb_active splits at all (i.e. isn't a bouldering activity)."""
    climbs = [s for s in splits if s.climb_grade is not None]
    if not climbs:
        return None, None, None
    completed_grades = [c.climb_grade for c in climbs if c.climb_result == "completed"]
    climb_time_s = sum(c.duration_s for c in climbs if c.duration_s is not None) or None
    return len(climbs), (max(completed_grades) if completed_grades else None), climb_time_s


@router.get("/activities")
def list_activities(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    sport: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page[ActivitySummary]:
    stream_exists = (
        select(activity_stream.c.activity_id)
        .where(activity_stream.c.activity_id == activity.c.id)
        .exists()
    )
    # Whole-activity avg/max heart rate isn't a fixed column on `activity` (see
    # docs/DATA_DICTIONARY.md) -- it's an EAV metric, one row per activity, under one of two
    # possible metric_key namespaces (see AVG_HR_METRIC_KEYS above). Correlated scalar
    # subqueries keep this a single query, matching the existing `stream_exists` pattern
    # immediately above, rather than an N+1 per-activity lookup.
    avg_hr_subq = _aliased_metric_subquery(AVG_HR_METRIC_KEYS)
    max_hr_subq = _aliased_metric_subquery(MAX_HR_METRIC_KEYS)
    # Same EAV pattern as avg/max heart rate above. training_load_peak's stored value_num is
    # already the real Training Load figure -- the FIT SDK profile gives it `scale: 65536`,
    # which its own decoder applies before we ever see the value (confirmed by introspecting
    # the installed garmin_fit_sdk's profile.py, not assumed).
    training_load_subq = (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key == "fit.session.training_load_peak",
        )
        .limit(1)
        .scalar_subquery()
    )
    # workout_rpe's raw stored value is the Borg CR10 scale x10 (an unmapped/generic field, so
    # nothing descales it on ingest) -- fetched raw here, descaled below in Python alongside
    # get_activity's identical conversion, so the two code paths can't drift out of sync.
    workout_rpe_subq = (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key == "fit.session.workout_rpe",
        )
        .limit(1)
        .scalar_subquery()
    )
    # Same EAV pattern as training_load_peak above -- see performance.py's own docstring for how
    # this value is computed and kept fresh.
    vdot_subq = (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key == VDOT_METRIC_KEY,
        )
        .limit(1)
        .scalar_subquery()
    )
    # fit.user_profile.weight -- the athlete's recorded body weight at the time of this specific
    # activity (a generic per-session field, present on ~99% of the real archive). Exposed so the
    # frontend can derive MET-minutes without a second per-activity fetch (see ActivitySummary's
    # weight_kg docstring).
    weight_subq = (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key == "fit.user_profile.weight",
        )
        .limit(1)
        .scalar_subquery()
    )
    # activity_workout is a separate one-row-per-activity table (not activity_metric's EAV
    # pattern), but the same correlated-scalar-subquery approach avoids an N+1 per-card fetch for
    # list/day views -- see ActivitySummary.workout_name's own docstring for why this exists.
    workout_name_subq = (
        select(activity_workout.c.name)
        .where(activity_workout.c.activity_id == activity.c.id)
        .limit(1)
        .scalar_subquery()
    )
    # Bouldering-only summary fields, for the activity-card/day-view pill (see ActivitySummary's
    # own docstring) -- same correlated-scalar-subquery shape as everything else above, against
    # `split` rather than `activity_metric`. All three come back None for a non-bouldering
    # activity, since no split row matches `climb_grade IS NOT NULL` at all.
    climb_route_count_subq = (
        select(func.count())
        .select_from(split_table)
        .where(
            split_table.c.activity_id == activity.c.id,
            split_table.c.climb_grade.is_not(None),
        )
        .scalar_subquery()
    )
    climb_max_completed_grade_subq = (
        select(func.max(split_table.c.climb_grade))
        .where(
            split_table.c.activity_id == activity.c.id,
            split_table.c.climb_result == "completed",
        )
        .scalar_subquery()
    )
    climb_time_s_subq = (
        select(func.sum(split_table.c.duration_s))
        .where(
            split_table.c.activity_id == activity.c.id,
            split_table.c.split_type == "climb_active",
        )
        .scalar_subquery()
    )
    query = select(
        activity.c.id,
        activity.c.start_time_utc,
        activity.c.utc_offset_s,
        activity.c.local_date,
        activity.c.sport,
        activity.c.sub_sport,
        activity.c.name,
        activity.c.is_race,
        activity.c.duration_s,
        activity.c.moving_duration_s,
        activity.c.distance_m,
        activity.c.elevation_gain_m,
        activity.c.calories,
        activity.c.primary_source,
        stream_exists.label("stream_available"),
        avg_hr_subq.label("avg_hr_bpm"),
        max_hr_subq.label("max_hr_bpm"),
        training_load_subq.label("training_load"),
        workout_rpe_subq.label("workout_rpe_raw"),
        weight_subq.label("weight_kg"),
        vdot_subq.label("vdot"),
        workout_name_subq.label("workout_name"),
        climb_route_count_subq.label("climb_route_count"),
        climb_max_completed_grade_subq.label("climb_max_completed_grade"),
        climb_time_s_subq.label("climb_time_s"),
    ).where(activity.c.athlete_id == athlete_id, activity.c.deleted_at.is_(None))

    if start_date is not None:
        query = query.where(activity.c.local_date >= start_date.isoformat())
    if end_date is not None:
        query = query.where(activity.c.local_date <= end_date.isoformat())
    if sport is not None:
        query = query.where(activity.c.sport == sport)

    # Counted directly against `activity` -- not `select(func.count()).select_from(query.
    # subquery())`, which would force the engine to evaluate the avg/max heart rate correlated
    # subqueries (and stream_exists) for every matching row a second time just to discard them.
    count_query = select(func.count()).select_from(activity).where(
        activity.c.athlete_id == athlete_id, activity.c.deleted_at.is_(None)
    )
    if start_date is not None:
        count_query = count_query.where(activity.c.local_date >= start_date.isoformat())
    if end_date is not None:
        count_query = count_query.where(activity.c.local_date <= end_date.isoformat())
    if sport is not None:
        count_query = count_query.where(activity.c.sport == sport)
    total = conn.execute(count_query).scalar_one()

    rows = conn.execute(
        query.order_by(activity.c.start_time_utc.desc()).limit(limit).offset(offset)
    ).fetchall()

    items = [
        ActivitySummary(
            id=r.id,
            start_time_utc=to_utc(r.start_time_utc),
            utc_offset_s=r.utc_offset_s,
            local_date=r.local_date,
            sport=r.sport,
            sub_sport=r.sub_sport,
            name=r.name,
            is_race=r.is_race,
            duration_s=r.duration_s,
            moving_duration_s=r.moving_duration_s,
            distance_m=r.distance_m,
            elevation_gain_m=r.elevation_gain_m,
            calories=r.calories,
            avg_hr_bpm=r.avg_hr_bpm,
            max_hr_bpm=r.max_hr_bpm,
            training_load=r.training_load,
            workout_rpe=_workout_rpe_from_raw(r.workout_rpe_raw),
            weight_kg=r.weight_kg,
            vdot=r.vdot,
            workout_name=r.workout_name,
            primary_source=r.primary_source,
            stream_available=bool(r.stream_available),
            # COUNT() returns 0 (not NULL) when no split matches, unlike the MAX()/SUM()
            # subqueries above (both naturally NULL over zero rows) -- `or None` normalizes
            # that 0 to the same "not a bouldering activity" None the other two fields already
            # report, rather than a bouldering pill reading "0 routes" for a run.
            climb_route_count=r.climb_route_count or None,
            climb_max_completed_grade=r.climb_max_completed_grade,
            climb_time_s=r.climb_time_s,
        )
        for r in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


# Registered before /activities/{activity_id} -- FastAPI matches routes in registration order,
# and /activities/years would otherwise be swallowed by {activity_id} (with activity_id="years").
@router.get("/activities/years")
def list_activity_years(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[int]:
    """Distinct years with at least one activity, descending -- powers DateNavigator's year
    strip. A dedicated cheap aggregate instead of paginating through every activity summary
    just to read `local_date`'s year off each one (which is what this replaced -- with activity
    count now in the thousands, that full-history fetch had become the single slowest thing on
    every calendar page navigation, since DateNavigator renders on every one of them)."""
    rows = conn.execute(
        select(func.substr(activity.c.local_date, 1, 4))
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            activity.c.local_date.is_not(None),
        )
        .distinct()
    ).scalars().all()
    return sorted({int(y) for y in rows if y}, reverse=True)


# Registered before /activities/{activity_id} -- FastAPI matches routes in registration order,
# and /activities/map would otherwise be swallowed by {activity_id} (with activity_id="map").
@router.get("/activities/map")
def list_activity_map_points(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    sport: str | None = Query(None),
) -> list[ActivityMapPointOut]:
    """Every GPS-bearing activity's start point in one response -- the map explorer's whole data
    need (ADR 0011 decision 2). Real scale confirmed against the archive before building this:
    904 of 1250 activities have a route_geom row with a start point; the rest are indoor/no-GPS
    activities that correctly have none. That keeps this one unbounded query with no pagination,
    matching the phase's own "renders in under 1s" acceptance criterion -- 904 rows is nowhere
    near where pagination would start to matter.
    """
    query = (
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.sport,
            activity.c.name,
            activity.c.distance_m,
            route_geom.c.start_lat,
            route_geom.c.start_lng,
        )
        .select_from(activity.join(route_geom, route_geom.c.activity_id == activity.c.id))
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            route_geom.c.start_lat.is_not(None),
            route_geom.c.start_lng.is_not(None),
        )
    )
    if start_date is not None:
        query = query.where(activity.c.local_date >= start_date.isoformat())
    if end_date is not None:
        query = query.where(activity.c.local_date <= end_date.isoformat())
    if sport is not None:
        query = query.where(activity.c.sport == sport)

    rows = conn.execute(query).fetchall()
    return [
        ActivityMapPointOut(
            id=r.id,
            local_date=r.local_date,
            sport=r.sport,
            name=r.name,
            distance_m=r.distance_m,
            start_lat=r.start_lat,
            start_lng=r.start_lng,
        )
        for r in rows
    ]


# Registered before /activities/{activity_id} for the same route-ordering reason as
# /activities/map above.
@router.get("/activities/routes")
def list_activity_routes(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    ids: str = Query(..., description="Comma-separated activity ids"),
) -> list[ActivityRouteOut]:
    """Batch polyline lookup for the activity list / day view's thumbnail maps -- one request per
    page of cards (bounded by however many ids the caller sends, typically that page's own
    activity count) rather than one request per card. Activities with no route_geom row (no GPS)
    are simply absent from the response, not returned with a null polyline.
    """
    id_list = [i for i in (part.strip() for part in ids.split(",")) if i]
    if not id_list:
        return []
    # A generous cap, not a real observed ceiling -- this endpoint is only ever called with one
    # page's worth of ids (activity list pages at 50, day view at a handful), so 200 is headroom
    # against a misbehaving caller, not a tuned limit.
    id_list = id_list[:200]

    rows = conn.execute(
        select(route_geom.c.activity_id, route_geom.c.simplified_polyline).where(
            route_geom.c.athlete_id == athlete_id,
            route_geom.c.activity_id.in_(id_list),
            route_geom.c.simplified_polyline.is_not(None),
        )
    ).fetchall()
    return [
        ActivityRouteOut(id=r.activity_id, simplified_polyline=r.simplified_polyline)
        for r in rows
    ]


# Registered before /activities/{activity_id} -- same reason as list_activity_years above:
# "climbing-summary" would otherwise be swallowed as activity_id="climbing-summary".
@router.get("/activities/climbing-summary")
def get_climbing_summary(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
) -> ClimbingSummaryOut:
    """One bounded aggregate query over every bouldering session's own splits in
    `[start_date, end_date]` -- the weekly/monthly/yearly/all-time summary pages' "Climbing"
    section needs a true aggregate across however many sessions fall in the period (unlike
    `HikeStatsCard`'s client-side reduction over an already-fetched `ActivitySummary[]`, which
    works because every field it needs is already on that list response) plus the full per-grade
    attempted/completed distribution for the chart, which isn't a scalar summary at all.
    """
    where_clauses = [
        activity.c.athlete_id == athlete_id,
        activity.c.deleted_at.is_(None),
        activity.c.sub_sport == "bouldering",
    ]
    if start_date is not None:
        where_clauses.append(activity.c.local_date >= start_date.isoformat())
    if end_date is not None:
        where_clauses.append(activity.c.local_date <= end_date.isoformat())

    session_count = conn.execute(
        select(func.count()).select_from(activity).where(*where_clauses)
    ).scalar_one()

    climb_splits = conn.execute(
        select(
            split_table.c.climb_grade,
            split_table.c.climb_result,
            split_table.c.duration_s,
        )
        .select_from(split_table.join(activity, activity.c.id == split_table.c.activity_id))
        .where(*where_clauses, split_table.c.climb_grade.is_not(None))
    ).fetchall()

    total_climb_time_s = sum(s.duration_s for s in climb_splits if s.duration_s is not None)
    completed_grades = [s.climb_grade for s in climb_splits if s.climb_result == "completed"]

    breakdown_by_grade: dict[int, dict[str, int]] = {}
    for s in climb_splits:
        bucket = breakdown_by_grade.setdefault(s.climb_grade, {"attempted": 0, "completed": 0})
        # An unconfirmed "unknown_<n>" raw value (see fit/parser.py) is counted conservatively as
        # an attempt, never assumed completed.
        bucket["completed" if s.climb_result == "completed" else "attempted"] += 1

    return ClimbingSummaryOut(
        session_count=session_count,
        total_climb_time_s=total_climb_time_s,
        total_routes=len(climb_splits),
        max_completed_grade=max(completed_grades) if completed_grades else None,
        grade_breakdown=[
            ClimbGradeBreakdownOut(grade=grade, attempted=b["attempted"], completed=b["completed"])
            for grade, b in sorted(breakdown_by_grade.items())
        ],
    )


@router.get("/activities/{activity_id}")
def get_activity(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    con: duckdb.DuckDBPyConnection = Depends(get_duckdb),
    settings: Settings = Depends(get_settings),
) -> ActivityDetail:
    row = conn.execute(
        select(activity).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")

    stream_row = conn.execute(
        select(activity_stream.c.parquet_path).where(
            activity_stream.c.activity_id == activity_id
        )
    ).fetchone()

    # Detail-page-only, computed fresh every request rather than stored -- see
    # TransportMixFlagOut's own docstring for why this never runs list-wide.
    transport_mix_flag = None
    if stream_row is not None and row.sport in ELIGIBLE_SPORTS:
        result = detect_transport_mix(
            con, settings.parquet_dir / stream_row.parquet_path, sport=row.sport
        )
        if result is not None:
            transport_mix_flag = TransportMixFlagOut(
                at_start=result.at_start,
                at_end=result.at_end,
                suggested_trim_start_s=result.suggested_trim_start_s,
                suggested_trim_end_s=result.suggested_trim_end_s,
            )
    has_trim = (
        conn.execute(
            select(activity_trim_override.c.id).where(
                activity_trim_override.c.athlete_id == athlete_id,
                activity_trim_override.c.activity_start_time_utc == row.start_time_utc,
            )
        ).fetchone()
        is not None
    )

    device_row = None
    if row.device_id is not None:
        device_row = conn.execute(
            select(device_table).where(device_table.c.id == row.device_id)
        ).fetchone()

    laps = conn.execute(
        select(lap).where(lap.c.activity_id == activity_id).order_by(lap.c.lap_index)
    ).fetchall()
    splits = conn.execute(
        select(split_table)
        .where(split_table.c.activity_id == activity_id)
        .order_by(split_table.c.split_index)
    ).fetchall()
    route_row = conn.execute(
        select(route_geom).where(route_geom.c.activity_id == activity_id)
    ).fetchone()
    metrics = conn.execute(
        select(activity_metric).where(activity_metric.c.activity_id == activity_id)
    ).fetchall()
    workout_name_row = conn.execute(
        select(activity_workout.c.name).where(activity_workout.c.activity_id == activity_id)
    ).fetchone()
    metrics_by_key = {m.metric_key: m.value_num for m in metrics}

    estimated_sweat_loss_ml = None
    if row.duration_s is not None:
        activity_end = row.start_time_utc + timedelta(seconds=row.duration_s)
        estimated_sweat_loss_ml = _estimated_sweat_loss_ml(conn, athlete_id, activity_end)

    climb_route_count, climb_max_completed_grade, climb_time_s = _climb_summary_from_splits(
        splits
    )

    return ActivityDetail(
        id=row.id,
        start_time_utc=to_utc(row.start_time_utc),
        utc_offset_s=row.utc_offset_s,
        local_date=row.local_date,
        sport=row.sport,
        sub_sport=row.sub_sport,
        name=row.name,
        is_race=row.is_race,
        duration_s=row.duration_s,
        distance_m=row.distance_m,
        elevation_gain_m=row.elevation_gain_m,
        calories=row.calories,
        avg_hr_bpm=_first_metric(metrics_by_key, AVG_HR_METRIC_KEYS),
        max_hr_bpm=_first_metric(metrics_by_key, MAX_HR_METRIC_KEYS),
        training_load=metrics_by_key.get("fit.session.training_load_peak"),
        workout_rpe=_workout_rpe_from_raw(metrics_by_key.get("fit.session.workout_rpe")),
        weight_kg=metrics_by_key.get("fit.user_profile.weight"),
        vdot=metrics_by_key.get(VDOT_METRIC_KEY),
        workout_name=workout_name_row.name if workout_name_row is not None else None,
        primary_source=row.primary_source,
        stream_available=stream_row is not None,
        climb_route_count=climb_route_count,
        climb_max_completed_grade=climb_max_completed_grade,
        climb_time_s=climb_time_s,
        moving_duration_s=row.moving_duration_s,
        estimated_sweat_loss_ml=estimated_sweat_loss_ml,
        carbohydrates_g=row.carbohydrates_g,
        sodium_mg=row.sodium_mg,
        transport_mix_flag=transport_mix_flag,
        has_trim=has_trim,
        device=(
            DeviceOut(
                manufacturer=device_row.manufacturer,
                product=device_row.product,
                serial_number=device_row.serial_number,
            )
            if device_row is not None
            else None
        ),
        laps=[
            LapOut(
                lap_index=lap_row.lap_index,
                start_time_utc=to_utc(lap_row.start_time_utc),
                duration_s=lap_row.duration_s,
                moving_duration_s=lap_row.moving_duration_s,
                distance_m=lap_row.distance_m,
                avg_hr=lap_row.avg_hr,
                max_hr=lap_row.max_hr,
                avg_speed_mps=lap_row.avg_speed_mps,
            )
            for lap_row in laps
        ],
        splits=[
            SplitOut(
                split_index=split_row.split_index,
                split_type=split_row.split_type,
                start_time_utc=(
                    to_utc(split_row.start_time_utc) if split_row.start_time_utc else None
                ),
                end_time_utc=to_utc(split_row.end_time_utc) if split_row.end_time_utc else None,
                duration_s=split_row.duration_s,
                distance_m=split_row.distance_m,
                climb_grade=split_row.climb_grade,
                climb_result=split_row.climb_result,
                climb_avg_hr=split_row.climb_avg_hr,
                climb_max_hr=split_row.climb_max_hr,
                is_manual=split_row.is_manual,
            )
            for split_row in splits
        ],
        route=(
            RouteOut(
                encoded_polyline=route_row.encoded_polyline,
                min_lat=route_row.min_lat,
                min_lng=route_row.min_lng,
                max_lat=route_row.max_lat,
                max_lng=route_row.max_lng,
                start_lat=route_row.start_lat,
                start_lng=route_row.start_lng,
                end_lat=route_row.end_lat,
                end_lng=route_row.end_lng,
            )
            if route_row is not None
            else None
        ),
        metrics=[
            ActivityMetricOut(
                metric_key=m.metric_key,
                value_num=m.value_num,
                value_text=m.value_text,
                unit=m.unit,
                source=m.source,
            )
            for m in metrics
        ],
    )


_CONTEXT_DISTANCE_BAND_FRACTION = 0.15
_CONTEXT_RECENT_WINDOW_DAYS = 90
_CONTEXT_FASTEST_LIMIT = 30


@router.get("/activities/{activity_id}/context")
def get_activity_context(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityContextOut:
    """A deliberately small, honest comparison view -- not a reproduction of Garmin/intervals.icu's
    proprietary Performance Condition/SPI models, which this project has no way to replicate (see
    CLAUDE.md's "never invent a plausible-looking number" rule).

    `percentile_rank`: the share of this athlete's *other* same-sport activities within +/-15% of
    this one's distance that this activity was faster than or equal to (by effective pace,
    moving-duration-preferred). None when there's nothing to compare against yet -- a first
    5K has no percentile, and that's the honest answer, not 0 or 100.

    `recent`: same-sport activities in the 90 days up to and including this one's own date (not
    "today" -- viewing an old activity should show its own contemporaries), for a compact
    sparkline/bubble strip. Computed as a direct query against `activity`, not rollup-backed --
    matching the existing precedent that `/activities` itself queries `activity` directly, since
    this is a bounded single-activity lookup, not a decade-spanning dashboard aggregate.
    """
    row = conn.execute(
        select(
            activity.c.sport,
            activity.c.local_date,
            activity.c.distance_m,
            activity.c.duration_s,
            activity.c.moving_duration_s,
        ).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")

    effective_duration_expr = func.coalesce(activity.c.moving_duration_s, activity.c.duration_s)
    this_effective_duration = (
        row.moving_duration_s if row.moving_duration_s is not None else row.duration_s
    )

    percentile_rank: float | None = None
    comparable_count = 0
    fastest: list[ActivityContextRecentOut] = []
    if (
        row.distance_m is not None
        and row.distance_m > 0
        and this_effective_duration is not None
        and this_effective_duration > 0
    ):
        low = row.distance_m * (1 - _CONTEXT_DISTANCE_BAND_FRACTION)
        high = row.distance_m * (1 + _CONTEXT_DISTANCE_BAND_FRACTION)
        band_rows = conn.execute(
            select(
                activity.c.id,
                activity.c.local_date,
                activity.c.distance_m,
                effective_duration_expr.label("eff_s"),
                _aliased_metric_subquery(AVG_HR_METRIC_KEYS).label("avg_hr"),
            ).where(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport == row.sport,
                activity.c.distance_m >= low,
                activity.c.distance_m <= high,
                effective_duration_expr.is_not(None),
                effective_duration_expr > 0,
            )
        ).fetchall()

        this_pace_s_per_m = this_effective_duration / row.distance_m
        # Seconds-per-metre for each *other* comparable activity -- lower is faster. Excluding
        # self from both the count and the comparison is what makes comparable_count == 0 mean
        # "nothing to rank against yet" rather than always at least 1 (itself).
        other_paces = [
            r.eff_s / r.distance_m for r in band_rows if r.id != activity_id and r.distance_m > 0
        ]
        comparable_count = len(other_paces)
        if comparable_count > 0:
            slower_or_equal = sum(1 for p in other_paces if p >= this_pace_s_per_m)
            percentile_rank = round(100 * slower_or_equal / comparable_count, 1)

        # "Fastest 30 runs for the same distance": a *separate*, tighter query than the +/-15%
        # band `percentile_rank` draws on above -- "only the runs exactly between 26.00km and
        # 26.99km for a 26km activity, nothing else" -- the same whole-kilometre bucket as this
        # activity's own floor(distance_m / 1000), not a percentage band. The two aren't
        # interchangeable: at short distances +/-15% is narrower than a full km bucket (a 5K's
        # 15% band is +/-0.75km, missing part of the [5000, 6000) bucket), so this can't be
        # filtered out of `band_rows` in Python -- it needs its own query.
        km_floor_m = int(row.distance_m // 1000) * 1000
        fastest_rows = conn.execute(
            select(
                activity.c.id,
                activity.c.local_date,
                activity.c.distance_m,
                effective_duration_expr.label("eff_s"),
                _aliased_metric_subquery(AVG_HR_METRIC_KEYS).label("avg_hr"),
            ).where(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport == row.sport,
                activity.c.distance_m >= km_floor_m,
                activity.c.distance_m < km_floor_m + 1000,
                effective_duration_expr.is_not(None),
                effective_duration_expr > 0,
            )
        ).fetchall()

        # This activity itself is included if it earns a spot, not filtered out the way
        # `other_paces` above filters it for the percentile math.
        fastest = [
            ActivityContextRecentOut(
                id=r.id,
                local_date=r.local_date,
                distance_m=r.distance_m,
                duration_s=r.eff_s,
                avg_hr_bpm=r.avg_hr,
            )
            for r in sorted(fastest_rows, key=lambda r: r.eff_s / r.distance_m)[
                :_CONTEXT_FASTEST_LIMIT
            ]
        ]

    recent: list[ActivityContextRecentOut] = []
    if row.local_date is not None:
        window_start_date = date.fromisoformat(row.local_date) - timedelta(
            days=_CONTEXT_RECENT_WINDOW_DAYS
        )
        window_start = window_start_date.isoformat()
        recent_rows = conn.execute(
            select(
                activity.c.id,
                activity.c.local_date,
                activity.c.distance_m,
                effective_duration_expr.label("eff_s"),
            )
            .where(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport == row.sport,
                activity.c.local_date >= window_start,
                activity.c.local_date <= row.local_date,
                activity.c.distance_m.is_not(None),
                effective_duration_expr.is_not(None),
            )
            .order_by(activity.c.local_date.asc())
        ).fetchall()
        recent = [
            ActivityContextRecentOut(
                id=r.id, local_date=r.local_date, distance_m=r.distance_m, duration_s=r.eff_s
            )
            for r in recent_rows
        ]

    return ActivityContextOut(
        percentile_rank=percentile_rank,
        comparable_count=comparable_count,
        recent=recent,
        fastest=fastest,
    )


_COMPARISON_DISTANCE_BAND_FRACTION = 0.15  # same tolerance get_activity_context's own band uses
# Calibrated against this project's real archive (983 running activities with a recorded GPS
# start point), not picked arbitrarily: for every sampled activity, the same-location cluster
# saturates by ~200-300m (adding at most a handful more matches out to 500m) and then a real gap
# opens up before the next-nearest distinct location, first appearing between roughly 500m and
# 1000m away. 300m sits comfortably inside that gap -- loose enough to tolerate a slow GPS fix
# at the very start of a run, tight enough not to fold in a genuinely different starting point.
_COMPARISON_START_RADIUS_M = 300.0
_COMPARISON_LIMIT = 10


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres -- same formula as gpx/parser.py's own `_haversine_m`,
    duplicated rather than imported since that one is GPX-parsing-private and takes a different
    argument shape (lat/lon tuples); not worth a new shared module for one small function (see
    CLAUDE.md's cross-language/cross-module small-algorithm duplication precedent)."""
    p1, p2 = radians(lat1), radians(lat2)
    dlat = p2 - p1
    dlng = radians(lng2) - radians(lng1)
    h = sin(dlat / 2) ** 2 + cos(p1) * cos(p2) * sin(dlng / 2) ** 2
    return 2 * 6_371_000.0 * asin(sqrt(h))


@router.get("/activities/{activity_id}/comparisons")
def get_activity_comparisons(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityComparisonsOut:
    """The 10 most recent *other* activities of the same sport, within +/-15% of this one's own
    distance (same band `get_activity_context` uses) AND starting within 300m of this one's own
    start point (`_COMPARISON_START_RADIUS_M`, calibrated against real data -- see its own
    comment) -- e.g. "how did today's 10K from home compare to my last 10 10Ks from home."

    Same "deliberately small, honest comparison view" posture as `get_activity_context` (see that
    endpoint's own docstring): real per-activity numbers, no fabricated composite score. Empty
    `rows` (with `matched_count == 0`) whenever this activity has no distance, no recorded GPS
    start point (e.g. a treadmill run), or genuinely no location-and-distance match yet -- never
    a fabricated comparison for a route run for the first time.
    """
    row = conn.execute(
        select(
            activity.c.sport,
            activity.c.distance_m,
            route_geom.c.start_lat,
            route_geom.c.start_lng,
        )
        .select_from(activity.outerjoin(route_geom, route_geom.c.activity_id == activity.c.id))
        .where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")

    rows: list[ActivityComparisonRowOut] = []
    matched_count = 0
    if (
        row.distance_m is not None
        and row.distance_m > 0
        and row.start_lat is not None
        and row.start_lng is not None
    ):
        effective_duration_expr = func.coalesce(activity.c.moving_duration_s, activity.c.duration_s)
        low = row.distance_m * (1 - _COMPARISON_DISTANCE_BAND_FRACTION)
        high = row.distance_m * (1 + _COMPARISON_DISTANCE_BAND_FRACTION)
        candidate_rows = conn.execute(
            select(
                activity.c.id,
                activity.c.local_date,
                activity.c.start_time_utc,
                activity.c.distance_m,
                effective_duration_expr.label("eff_s"),
                route_geom.c.start_lat,
                route_geom.c.start_lng,
                _aliased_metric_subquery(AVG_HR_METRIC_KEYS).label("avg_hr"),
                _aliased_metric_subquery((VDOT_METRIC_KEY,)).label("vdot"),
                _aliased_metric_subquery((AVG_GAP_METRIC_KEY,)).label("avg_gap_speed_mps"),
                _aliased_metric_subquery((CADENCE_METRIC_KEY,)).label("cadence_raw"),
            )
            .select_from(activity.join(route_geom, route_geom.c.activity_id == activity.c.id))
            .where(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport == row.sport,
                activity.c.id != activity_id,
                activity.c.distance_m >= low,
                activity.c.distance_m <= high,
                effective_duration_expr.is_not(None),
                effective_duration_expr > 0,
                route_geom.c.start_lat.is_not(None),
                route_geom.c.start_lng.is_not(None),
            )
        ).fetchall()

        nearby = [
            r
            for r in candidate_rows
            if _haversine_m(row.start_lat, row.start_lng, r.start_lat, r.start_lng)
            <= _COMPARISON_START_RADIUS_M
        ]
        matched_count = len(nearby)
        # Most recent first -- start_time_utc as the tie-break for same-local_date activities,
        # not just local_date alone.
        nearby.sort(key=lambda r: (r.local_date or "", r.start_time_utc), reverse=True)
        rows = [
            ActivityComparisonRowOut(
                id=r.id,
                local_date=r.local_date,
                distance_m=r.distance_m,
                duration_s=r.eff_s,
                vdot=r.vdot,
                avg_gap_speed_mps=r.avg_gap_speed_mps,
                avg_hr_bpm=r.avg_hr,
                avg_cadence_spm=r.cadence_raw * 2 if r.cadence_raw is not None else None,
            )
            for r in nearby[:_COMPARISON_LIMIT]
        ]

    return ActivityComparisonsOut(
        start_radius_m=_COMPARISON_START_RADIUS_M,
        distance_band_fraction=_COMPARISON_DISTANCE_BAND_FRACTION,
        matched_count=matched_count,
        rows=rows,
    )


# Same +/-15% tolerance convention as _COMPARISON_DISTANCE_BAND_FRACTION above, applied to total
# activity duration (climb + rest) instead of distance -- bouldering has no distance at all, and
# "how long the gym session ran" is the natural analogue of "how far the run was" here. No
# location matching: unlike an outdoor run, a bouldering session is already implicitly "at the
# gym", so there's no separate signal to match on the way GPS start point was for running.
_CLIMB_COMPARISON_DURATION_BAND_FRACTION = 0.15
_CLIMB_COMPARISON_LIMIT = 10


@router.get("/activities/{activity_id}/climb-comparisons")
def get_activity_climb_comparisons(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ClimbComparisonsOut:
    """The 10 most recent *other* bouldering sessions within +/-15% of this one's own total
    duration (climb + rest) -- e.g. "how did today's ~2hr session compare to my last 10 sessions
    of about the same length." Same honest-comparison posture as `get_activity_comparisons`: real
    per-session numbers (route count, max completed grade, climb time), no fabricated score.
    Empty `rows` (with `matched_count == 0`) whenever this activity has no duration recorded, or
    genuinely no other session of a similar length yet.
    """
    row = conn.execute(
        select(activity.c.sub_sport, activity.c.duration_s).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")

    rows: list[ClimbComparisonRowOut] = []
    matched_count = 0
    if row.duration_s is not None and row.duration_s > 0:
        low = row.duration_s * (1 - _CLIMB_COMPARISON_DURATION_BAND_FRACTION)
        high = row.duration_s * (1 + _CLIMB_COMPARISON_DURATION_BAND_FRACTION)
        route_count_subq = (
            select(func.count())
            .select_from(split_table)
            .where(
                split_table.c.activity_id == activity.c.id,
                split_table.c.climb_grade.is_not(None),
            )
            .scalar_subquery()
        )
        max_completed_grade_subq = (
            select(func.max(split_table.c.climb_grade))
            .where(
                split_table.c.activity_id == activity.c.id,
                split_table.c.climb_result == "completed",
            )
            .scalar_subquery()
        )
        climb_time_s_subq = (
            select(func.sum(split_table.c.duration_s))
            .where(
                split_table.c.activity_id == activity.c.id,
                split_table.c.split_type == "climb_active",
            )
            .scalar_subquery()
        )
        candidate_rows = conn.execute(
            select(
                activity.c.id,
                activity.c.local_date,
                activity.c.start_time_utc,
                activity.c.duration_s,
                route_count_subq.label("route_count"),
                max_completed_grade_subq.label("max_completed_grade"),
                climb_time_s_subq.label("climb_time_s"),
            ).where(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sub_sport == row.sub_sport,
                activity.c.id != activity_id,
                activity.c.duration_s >= low,
                activity.c.duration_s <= high,
            )
        ).fetchall()

        matched_count = len(candidate_rows)
        # Most recent first -- start_time_utc as the tie-break for same-local_date activities.
        ordered = sorted(
            candidate_rows, key=lambda r: (r.local_date or "", r.start_time_utc), reverse=True
        )
        rows = [
            ClimbComparisonRowOut(
                id=r.id,
                local_date=r.local_date,
                duration_s=r.duration_s,
                route_count=r.route_count,
                max_completed_grade=r.max_completed_grade,
                climb_time_s=r.climb_time_s,
            )
            for r in ordered[:_CLIMB_COMPARISON_LIMIT]
        ]

    return ClimbComparisonsOut(
        duration_band_fraction=_CLIMB_COMPARISON_DURATION_BAND_FRACTION,
        matched_count=matched_count,
        rows=rows,
    )


@router.patch("/activities/{activity_id}/climb-routes/{split_index}")
def patch_climb_route_status(
    activity_id: str,
    split_index: int,
    body: ClimbRouteStatusIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> SplitOut:
    """A manual "I logged the wrong status" correction for one route -- see
    bouldering_overrides.py's own docstring for why this is a durable, rebuild-safe correction
    table rather than a one-off column mutation."""
    try:
        set_route_status_override(
            conn,
            athlete_id=athlete_id,
            activity_id=activity_id,
            split_index=split_index,
            result=body.result,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    conn.commit()
    return _get_split_out(
        conn, athlete_id=athlete_id, activity_id=activity_id, split_index=split_index
    )


@router.post("/activities/{activity_id}/climb-routes")
def post_climb_route(
    activity_id: str,
    body: ClimbRouteAddIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> SplitOut:
    """A route the device never tracked at all -- forgotten to start/stop tracking, climbed
    after the watch was already stopped, etc. See bouldering_overrides.py's own docstring for
    why this is a durable, rebuild-safe record rather than a one-off row insert."""
    if body.result not in {"attempt", "completed"}:
        raise HTTPException(
            status_code=422, detail="result must be one of ['attempt', 'completed']"
        )
    try:
        split_index = add_manual_route(
            conn,
            athlete_id=athlete_id,
            activity_id=activity_id,
            grade=body.grade,
            result=body.result,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    conn.commit()
    return _get_split_out(
        conn, athlete_id=athlete_id, activity_id=activity_id, split_index=split_index
    )


@router.delete("/activities/{activity_id}/climb-routes/{split_index}", status_code=204)
def delete_climb_route(
    activity_id: str,
    split_index: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> None:
    """Removes a manually-added route -- never a FIT-derived one (see
    bouldering_overrides.py::delete_manual_route's own docstring for why that's never allowed:
    it would just be re-derived by the next ingest/rebuild regardless)."""
    try:
        delete_manual_route(
            conn, athlete_id=athlete_id, activity_id=activity_id, split_index=split_index
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    conn.commit()


def _get_split_out(
    conn: Connection, *, athlete_id: str, activity_id: str, split_index: int
) -> SplitOut:
    row = conn.execute(
        select(split_table).where(
            split_table.c.athlete_id == athlete_id,
            split_table.c.activity_id == activity_id,
            split_table.c.split_index == split_index,
        )
    ).fetchone()
    assert row is not None  # the caller just wrote this exact row
    return SplitOut(
        split_index=row.split_index,
        split_type=row.split_type,
        start_time_utc=to_utc(row.start_time_utc) if row.start_time_utc else None,
        end_time_utc=to_utc(row.end_time_utc) if row.end_time_utc else None,
        duration_s=row.duration_s,
        distance_m=row.distance_m,
        climb_grade=row.climb_grade,
        climb_result=row.climb_result,
        climb_avg_hr=row.climb_avg_hr,
        climb_max_hr=row.climb_max_hr,
        is_manual=row.is_manual,
    )


@router.get("/activities/{activity_id}/insights")
def get_activity_insights(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[InsightOut]:
    """Point-in-time insights for this one activity -- e.g. "your fastest 10 km to date" or a
    "current streak" -- computed fresh at request time, not read from the athlete-wide `insight`
    table (unlike GET /insights). Always bounded to activities at-or-before this one's own
    `local_date`, never anything that happened later: browsing an old run must never leak
    knowledge of runs that hadn't happened yet. See insights/rules_activity.py.
    """
    row = conn.execute(
        select(activity.c.local_date).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")
    if row.local_date is None:
        return []

    as_of = date.fromisoformat(row.local_date)
    all_activities = load_insight_activities(conn, athlete_id)
    bounded = [a for a in all_activities if a.local_date <= row.local_date]
    insights = compute_activity_insights(activity_id, bounded, as_of)

    computed_at = datetime.now(UTC)
    return [
        InsightOut(
            kind=i.kind,
            window=i.window,
            title=i.title,
            detail=i.detail,
            value_num=i.value_num,
            metric_key=i.metric_key,
            sport_family=i.sport_family,
            activity_id=i.activity_id,
            local_date=i.local_date,
            computed_at=computed_at,
        )
        for i in insights
    ]


@router.get("/activities/{activity_id}/weather")
def get_activity_weather(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> ActivityWeatherOut:
    """Temperature/humidity range, feels-like temperature, wind, and a representative WMO
    weather code for this activity's own time window, sourced from Open-Meteo's historical
    archive (see weather.py's own docstring for the raw-first/cache-forever design and why
    feels-like/wind are single values rather than a range). `available=False` -- never a
    fabricated range -- whenever the activity has no GPS start point to query against, or the
    fetch/parse comes back empty (e.g. Open-Meteo unreachable)."""
    row = conn.execute(
        select(
            activity.c.start_time_utc,
            activity.c.duration_s,
            route_geom.c.start_lat,
            route_geom.c.start_lng,
        )
        .select_from(activity.outerjoin(route_geom, activity.c.id == route_geom.c.activity_id))
        .where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")

    if row.start_lat is None or row.start_lng is None or row.duration_s is None:
        return ActivityWeatherOut(available=False)

    summary = get_or_fetch_activity_weather(
        conn,
        settings.raw_archive_dir,
        athlete_id=athlete_id,
        activity_id=activity_id,
        start_time_utc=to_utc(row.start_time_utc),
        duration_s=row.duration_s,
        lat=row.start_lat,
        lon=row.start_lng,
    )
    conn.commit()
    if summary is None:
        return ActivityWeatherOut(available=False)

    return ActivityWeatherOut(
        available=True,
        temperature_min_c=summary.temperature_min_c,
        temperature_max_c=summary.temperature_max_c,
        humidity_min_pct=summary.humidity_min_pct,
        humidity_max_pct=summary.humidity_max_pct,
        weather_code=summary.weather_code,
        feels_like_c=summary.feels_like_c,
        wind_speed_mps=summary.wind_speed_mps,
        wind_direction_deg=summary.wind_direction_deg,
    )


@router.get("/activities/{activity_id}/location")
def get_activity_location(
    activity_id: str,
    background_tasks: BackgroundTasks,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> ActivityLocationOut:
    """City/town or national park name for this activity's own GPS start point, reverse-
    geocoded via Nominatim (see geocoding.py's own docstring for the raw-first/cache-forever
    design). `available=False` -- never a fabricated name -- whenever the activity has no GPS
    start point to query against, nothing is cached yet, or the fetch/parse came back empty.

    A cache miss never blocks this response on Nominatim's own network latency or this
    project's 1-req/s throttle (geocoding.py) -- the real fetch runs as a background task after
    the response is already sent, so viewing an activity for the first time is never the slow
    one; its location just appears on the next view once cached. `sync backfill-locations`
    (cli.py) pre-warms this cache for the whole existing archive so that in practice almost
    every activity is already cached by the time it's ever viewed. Weather (the sibling
    endpoint just above) still fetches inline -- Open-Meteo has no comparable rate limit to
    respect, so there was never a reason to defer it."""
    row = conn.execute(
        select(route_geom.c.start_lat, route_geom.c.start_lng)
        .select_from(activity.outerjoin(route_geom, activity.c.id == route_geom.c.activity_id))
        .where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")

    if row.start_lat is None or row.start_lng is None:
        return ActivityLocationOut(available=False)

    cached = read_cached_location(conn, athlete_id, activity_id)
    if cached is not None:
        return ActivityLocationOut(available=True, location_name=cached)

    lat, lon = row.start_lat, row.start_lng

    def _fetch_in_background() -> None:
        with engine.connect() as bg_conn:
            get_or_fetch_activity_location(
                bg_conn,
                settings.raw_archive_dir,
                athlete_id=athlete_id,
                activity_id=activity_id,
                lat=lat,
                lon=lon,
            )
            bg_conn.commit()

    background_tasks.add_task(_fetch_in_background)
    return ActivityLocationOut(available=False)


@router.get("/activities/{activity_id}/workout")
def get_activity_workout(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityWorkoutOut | None:
    """The pre-planned workout structure recorded into this activity's own FIT file (Garmin
    Connect's "Workout" builder, downloaded to the device before the activity) -- `None` for the
    (large majority of) activities with no such plan, never a fabricated one. See
    fit/parser.py::_parse_workout's own docstring for how this is parsed and why it's stored
    unexpanded (raw `repeat_until_steps_cmplt` steps, not pre-flattened repetitions)."""
    exists = conn.execute(
        select(activity.c.id).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="activity not found")

    workout_row = conn.execute(
        select(activity_workout.c.name, activity_workout.c.description).where(
            activity_workout.c.activity_id == activity_id
        )
    ).fetchone()
    if workout_row is None:
        return None

    step_rows = conn.execute(
        select(activity_workout_step)
        .where(activity_workout_step.c.activity_id == activity_id)
        .order_by(activity_workout_step.c.step_index)
    ).fetchall()

    return ActivityWorkoutOut(
        name=workout_row.name,
        description=workout_row.description,
        steps=[
            ActivityWorkoutStepOut(
                step_index=s.step_index,
                duration_type=s.duration_type,
                duration_time_s=s.duration_time_s,
                duration_distance_m=s.duration_distance_m,
                target_type=s.target_type,
                target_low_mps=s.target_low_mps,
                target_high_mps=s.target_high_mps,
                intensity=s.intensity,
                repeat_from_step=s.repeat_from_step,
                repeat_count=s.repeat_count,
            )
            for s in step_rows
        ],
    )


@router.get("/activities/{activity_id}/sources")
def get_activity_sources(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivitySourcesOut:
    """Every source this activity's data actually came from, plus the merge decisions that
    linked them together -- the "both sources inspectable" half of the Phase 8 acceptance
    criterion (see ADR 0012). The other half, undoing a wrong merge, is
    POST .../sources/{link_id}/split below."""
    exists = conn.execute(
        select(activity.c.id).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="activity not found")

    link_rows = conn.execute(
        select(activity_source_link)
        .where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.activity_id == activity_id,
        )
        .order_by(activity_source_link.c.ingested_at.asc())
    ).fetchall()
    can_split = len(link_rows) > 1

    decision_rows = conn.execute(
        select(merge_decision)
        .where(
            merge_decision.c.athlete_id == athlete_id,
            merge_decision.c.matched_activity_id == activity_id,
            merge_decision.c.decision == "matched",
        )
        .order_by(merge_decision.c.decided_at.asc())
    ).fetchall()

    return ActivitySourcesOut(
        sources=[
            ActivitySourceOut(
                link_id=r.id,
                source=r.source,
                external_id=r.external_id,
                ingested_at=to_utc(r.ingested_at),
                can_split=can_split,
            )
            for r in link_rows
        ],
        merge_decisions=[
            ActivityMergeDecisionOut(
                candidate_ref=r.candidate_ref,
                reasons=json.loads(r.reasons),
                decided_at=to_utc(r.decided_at),
            )
            for r in decision_rows
        ],
    )


@router.post("/activities/{activity_id}/sources/{link_id}/split")
def split_activity_source(
    activity_id: str,
    link_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> ActivitySplitOut:
    """Undoes a wrong merge: repoints one source's `activity_source_link` row onto a brand-new
    activity, re-derived from that source's own already-archived raw bytes -- never destructive,
    the original activity keeps every other source it had. Rejects with 400 if `link_id` is the
    activity's only source (nothing to split away from)."""
    all_links = conn.execute(
        select(activity_source_link).where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.activity_id == activity_id,
        )
    ).fetchall()
    if not all_links:
        raise HTTPException(status_code=404, detail="activity not found")
    target = next((r for r in all_links if r.id == link_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="source link not found on this activity")
    if len(all_links) <= 1:
        raise HTTPException(status_code=400, detail="cannot split the only source of an activity")

    raw_row = conn.execute(
        select(raw_object).where(raw_object.c.id == target.raw_object_id)
    ).fetchone()
    if raw_row is None:
        raise HTTPException(status_code=500, detail="raw object for this source is missing")

    content = read_raw_bytes(settings.raw_archive_dir, raw_row.storage_path)
    batch = reparse_raw_object(raw_row.kind, content)
    if batch.kind != "activity" or batch.activity is None:
        raise HTTPException(
            status_code=422, detail="this source's raw data can't be re-parsed into an activity"
        )
    parsed = batch.activity

    # GPX/TCX carry no session-level totals of their own (see gpx/parser.py, tcx/parser.py) --
    # the original ingest overlaid activities.csv's totals onto them, so a faithful split needs
    # the same overlay, re-fetched from that row's own archived raw_object.
    if raw_row.kind in ("strava_export_gpx", "strava_export_tcx"):
        csv_raw = conn.execute(
            select(raw_object)
            .where(
                raw_object.c.athlete_id == athlete_id,
                raw_object.c.source == "strava_export",
                raw_object.c.kind == "strava_export_csv_row",
                raw_object.c.external_id == target.external_id,
            )
            .order_by(raw_object.c.id.desc())
            .limit(1)
        ).fetchone()
        if csv_raw is not None:
            csv_content = read_raw_bytes(settings.raw_archive_dir, csv_raw.storage_path)
            row = dict(json.loads(csv_content))
            parsed = _overlay_csv_totals(parsed, row)

    device_id = _upsert_device(conn, athlete_id, parsed.device) if parsed.device else None
    new_activity_id = insert_new_activity(
        conn,
        settings.parquet_dir,
        athlete_id=athlete_id,
        source=target.source,
        device_id=device_id,
        a=parsed,
    )

    conn.execute(
        activity_source_link.update()
        .where(activity_source_link.c.id == link_id)
        .values(activity_id=new_activity_id)
    )

    touched_dates = {_local_date(parsed.start_time_utc, parsed.utc_offset_s)}
    old_row = conn.execute(
        select(activity.c.start_time_utc, activity.c.utc_offset_s).where(
            activity.c.id == activity_id
        )
    ).fetchone()
    if old_row is not None:
        touched_dates.add(_local_date(old_row.start_time_utc, old_row.utc_offset_s))
    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    conn.commit()

    return ActivitySplitOut(new_activity_id=new_activity_id)


@router.patch("/activities/{activity_id}/sport")
def override_activity_sport(
    activity_id: str,
    body: ActivitySportOverrideIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivitySportOverrideOut:
    """A manual "this sport is wrong" correction -- for activities whose raw source itself
    records the wrong sport (e.g. a hike recorded with a watch's "Run" profile still selected,
    or a FIT file reconstructed by a third-party tool that always writes `sport=running`
    regardless of what the activity actually was), where there's no second raw source in the
    archive to cross-check against automatically. See sport_override.py's own docstring for why
    this is a durable, rebuild-safe correction table rather than a one-off column mutation.
    """
    if not body.sport.strip():
        raise HTTPException(status_code=422, detail="sport must not be empty")
    try:
        set_sport_override(
            conn,
            athlete_id=athlete_id,
            activity_id=activity_id,
            sport=body.sport,
            sub_sport=body.sub_sport,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    conn.commit()
    return ActivitySportOverrideOut(sport=body.sport, sub_sport=body.sub_sport)


@router.patch("/activities/{activity_id}/race")
def override_activity_race(
    activity_id: str,
    body: ActivityRaceOverrideIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityRaceOverrideOut:
    """A manual "this is/isn't a race" correction -- for activities where Garmin's own
    `eventTypeId` heuristic (garmin_activity_summary.py) gets it wrong, e.g. a real race the
    athlete never flagged as one inside the Garmin Connect app itself, which is the only signal
    that heuristic has to go on. See sport_override.py's own docstring for why this is a
    durable, rebuild-safe correction table rather than a one-off column mutation.
    """
    try:
        set_race_override(
            conn, athlete_id=athlete_id, activity_id=activity_id, is_race=body.is_race
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    conn.commit()
    return ActivityRaceOverrideOut(is_race=body.is_race)


@router.patch("/activities/{activity_id}/name")
def override_activity_name(
    activity_id: str,
    body: ActivityNameOverrideIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityNameOverrideOut:
    """A manual "this title is wrong" correction -- Garmin Connect's own name for an activity is
    not reliably a real athlete-given title (it can be just as generic a template as the
    FIT-derived on-device default, confirmed the hard way -- see sport_override.py's own
    docstring), so there is no safe automatic rule for replacing a boring name like "Run". Only
    the athlete looking at one specific activity can tell. See sport_override.py's own docstring
    for why this is a durable, rebuild-safe correction table rather than a one-off column
    mutation.
    """
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="name must not be empty")
    try:
        set_name_override(conn, athlete_id=athlete_id, activity_id=activity_id, name=body.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    conn.commit()
    return ActivityNameOverrideOut(name=body.name)


@router.patch("/activities/{activity_id}/fueling")
def override_activity_fueling(
    activity_id: str,
    body: ActivityFuelingIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ActivityFuelingOut:
    """The athlete's own carbohydrate/sodium intake for this activity -- unlike the sport/race/
    name corrections above, there's no vendor source for this at all (neither the FIT profile
    nor the Garmin Connect API carry it, confirmed by introspecting both directly), so this is
    the athlete's only input, not a fix to something derived. See sport_override.py's own
    docstring for why this is a durable, rebuild-safe table rather than a one-off column
    mutation. Both fields are set together (like sport/sub_sport above) -- send the current value
    of whichever one you're not changing to avoid clearing it back to null.
    """
    for label, value in (("carbohydrates_g", body.carbohydrates_g), ("sodium_mg", body.sodium_mg)):
        if value is not None and value < 0:
            raise HTTPException(status_code=422, detail=f"{label} must not be negative")
    try:
        set_fueling_override(
            conn,
            athlete_id=athlete_id,
            activity_id=activity_id,
            carbohydrates_g=body.carbohydrates_g,
            sodium_mg=body.sodium_mg,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    conn.commit()
    return ActivityFuelingOut(carbohydrates_g=body.carbohydrates_g, sodium_mg=body.sodium_mg)


def _refresh_after_trim_change(
    conn: Connection, *, athlete_id: str, local_date: str | None
) -> None:
    """The rollup/fitness/insights cascade a trim commit or undo needs -- the first PATCH/POST-
    style correction endpoint in this router to need it, since every other correction here
    (sport/race/name/fueling, bouldering route status, source split) leaves distance/duration/
    training_load untouched. Every ingest entry point (fit_folder, garmin_export, garmin_connect,
    rebuild) already runs this exact three-call sequence after touching a date -- see e.g.
    rebuild.py's own tail."""
    if local_date is None:
        return
    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates={local_date})
    refresh_fitness_rollup(conn, athlete_id=athlete_id)
    refresh_insights(conn, athlete_id=athlete_id)


@router.post("/activities/{activity_id}/trim")
def post_activity_trim(
    activity_id: str,
    body: ActivityTrimIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    con: duckdb.DuckDBPyConnection = Depends(get_duckdb),
    settings: Settings = Depends(get_settings),
) -> ActivityDetail:
    """Trims a stretch of car travel from the start and/or end of a hiking/walking recording --
    see activity_trim.py's own docstring for exactly what gets recomputed (distance, duration,
    elevation, heart rate, route, laps) versus cleared (calories, training load, neither
    honestly re-derivable from just the kept window)."""
    if body.trim_start_s is None and body.trim_end_s is None:
        raise HTTPException(
            status_code=422, detail="at least one of trim_start_s/trim_end_s is required"
        )
    activity_row = conn.execute(
        select(activity.c.local_date).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if activity_row is None:
        raise HTTPException(status_code=404, detail="activity not found")
    try:
        set_activity_trim(
            conn,
            con,
            settings.parquet_dir,
            athlete_id=athlete_id,
            activity_id=activity_id,
            trim_start_s=body.trim_start_s,
            trim_end_s=body.trim_end_s,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _refresh_after_trim_change(conn, athlete_id=athlete_id, local_date=activity_row.local_date)
    conn.commit()
    return get_activity(activity_id, athlete_id, conn=conn, con=con, settings=settings)


@router.delete("/activities/{activity_id}/trim")
def delete_activity_trim(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    con: duckdb.DuckDBPyConnection = Depends(get_duckdb),
    settings: Settings = Depends(get_settings),
) -> ActivityDetail:
    """Undoes a trim, restoring the pristine pre-trim state -- see activity_trim.py's own
    docstring for why this reparses the original raw bytes rather than recomputing with no
    window (which could never bring calories/training load back)."""
    activity_row = conn.execute(
        select(activity.c.local_date).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if activity_row is None:
        raise HTTPException(status_code=404, detail="activity not found")
    try:
        clear_activity_trim(
            conn, settings.raw_archive_dir, athlete_id=athlete_id, activity_id=activity_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _refresh_after_trim_change(conn, athlete_id=athlete_id, local_date=activity_row.local_date)
    conn.commit()
    return get_activity(activity_id, athlete_id, conn=conn, con=con, settings=settings)


@router.get("/activities/{activity_id}/stream")
def get_activity_stream(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    tier: str = Query("medium", pattern="^(low|medium|high)$"),
    channels: list[str] | None = Query(None),
    conn: Connection = Depends(get_conn),
    con: duckdb.DuckDBPyConnection = Depends(get_duckdb),
    settings: Settings = Depends(get_settings),
) -> StreamResponse:
    activity_row = conn.execute(
        select(activity.c.duration_s, activity.c.start_time_utc).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).fetchone()
    if activity_row is None:
        raise HTTPException(status_code=404, detail="activity not found")

    stream_row = conn.execute(
        select(
            activity_stream.c.parquet_path,
            activity_stream.c.channels,
            activity_stream.c.n_samples,
        ).where(activity_stream.c.activity_id == activity_id)
    ).fetchone()
    if stream_row is None:
        raise HTTPException(status_code=404, detail="no stream data for this activity")

    available_channels = frozenset(json.loads(stream_row.channels))
    requested = channels or []

    # An active trim means the Parquet file itself still holds the full original recording (see
    # activity_trim.py's own "never destructive" docstring) -- every chart/map/mini-map should
    # still only ever see the kept window, so this filters at read time rather than trusting
    # every caller to already know about the trim.
    trim_row = conn.execute(
        select(
            activity_trim_override.c.trim_start_s, activity_trim_override.c.trim_end_s
        ).where(
            activity_trim_override.c.athlete_id == athlete_id,
            activity_trim_override.c.activity_start_time_utc == activity_row.start_time_utc,
        )
    ).fetchone()
    window = None
    if trim_row is not None:
        window = (trim_row.trim_start_s or 0.0, trim_row.trim_end_s or float("inf"))

    try:
        result = downsample(
            con,
            settings.parquet_dir / stream_row.parquet_path,
            tier=tier,
            channels=requested,
            available_channels=available_channels,
            duration_s=activity_row.duration_s or 0.0,
            n_samples=stream_row.n_samples,
            window=window,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    selected = requested or sorted(available_channels)
    return StreamResponse(
        activity_id=activity_id,
        tier=tier,
        channels=selected,
        timestamps=result.timestamps,
        series=result.series,
    )
