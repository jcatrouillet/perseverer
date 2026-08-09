"""GET /activities, GET /activities/{id}, GET /activities/{id}/stream."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, func, select

from sporthealth.api.dependencies import get_conn, get_duckdb, require_api_key
from sporthealth.api.schemas.activities import (
    ActivityContextOut,
    ActivityContextRecentOut,
    ActivityDetail,
    ActivityMetricOut,
    ActivitySummary,
    DeviceOut,
    LapOut,
    RouteOut,
    SplitOut,
)
from sporthealth.api.schemas.common import Page, to_utc
from sporthealth.api.schemas.streams import StreamResponse
from sporthealth.config import Settings, get_settings
from sporthealth.db.schema import activity, activity_metric, activity_stream, lap, route_geom
from sporthealth.db.schema import device as device_table
from sporthealth.db.schema import split as split_table
from sporthealth.stream_query import downsample

router = APIRouter()

# fit.session.workout_rpe is an unrecognized/generic field (see fit/parser.py's docstring), so
# nothing descales it on ingest -- its stored value_num is the raw FIT uint8, Borg CR10 x10 per
# the installed garmin_fit_sdk's profile.py (field 193: "Common Borg CR10 / 0-10 RPE scale,
# multiplied 10x"). One helper so list_activities and get_activity apply the exact same
# conversion rather than each hand-rolling `/ 10`.
def _workout_rpe_from_raw(raw: float | None) -> float | None:
    return raw / 10 if raw is not None else None


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
    # docs/DATA_DICTIONARY.md) -- it's a FIT session-message metric, one EAV row per activity.
    # Correlated scalar subqueries keep this a single query, matching the existing
    # `stream_exists` pattern immediately above, rather than an N+1 per-activity lookup.
    avg_hr_subq = (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key == "fit.session.avg_heart_rate",
        )
        .limit(1)
        .scalar_subquery()
    )
    max_hr_subq = (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key == "fit.session.max_heart_rate",
        )
        .limit(1)
        .scalar_subquery()
    )
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
    query = select(
        activity.c.id,
        activity.c.start_time_utc,
        activity.c.utc_offset_s,
        activity.c.local_date,
        activity.c.sport,
        activity.c.sub_sport,
        activity.c.name,
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
            duration_s=r.duration_s,
            moving_duration_s=r.moving_duration_s,
            distance_m=r.distance_m,
            elevation_gain_m=r.elevation_gain_m,
            calories=r.calories,
            avg_hr_bpm=r.avg_hr_bpm,
            max_hr_bpm=r.max_hr_bpm,
            training_load=r.training_load,
            workout_rpe=_workout_rpe_from_raw(r.workout_rpe_raw),
            primary_source=r.primary_source,
            stream_available=bool(r.stream_available),
        )
        for r in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/activities/{activity_id}")
def get_activity(
    activity_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
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
        select(activity_stream.c.activity_id).where(
            activity_stream.c.activity_id == activity_id
        )
    ).fetchone()

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
    metrics_by_key = {m.metric_key: m.value_num for m in metrics}

    return ActivityDetail(
        id=row.id,
        start_time_utc=to_utc(row.start_time_utc),
        utc_offset_s=row.utc_offset_s,
        local_date=row.local_date,
        sport=row.sport,
        sub_sport=row.sub_sport,
        name=row.name,
        duration_s=row.duration_s,
        distance_m=row.distance_m,
        elevation_gain_m=row.elevation_gain_m,
        calories=row.calories,
        avg_hr_bpm=metrics_by_key.get("fit.session.avg_heart_rate"),
        max_hr_bpm=metrics_by_key.get("fit.session.max_heart_rate"),
        training_load=metrics_by_key.get("fit.session.training_load_peak"),
        workout_rpe=_workout_rpe_from_raw(metrics_by_key.get("fit.session.workout_rpe")),
        primary_source=row.primary_source,
        stream_available=stream_row is not None,
        moving_duration_s=row.moving_duration_s,
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
    if (
        row.distance_m is not None
        and row.distance_m > 0
        and this_effective_duration is not None
        and this_effective_duration > 0
    ):
        low = row.distance_m * (1 - _CONTEXT_DISTANCE_BAND_FRACTION)
        high = row.distance_m * (1 + _CONTEXT_DISTANCE_BAND_FRACTION)
        band_rows = conn.execute(
            select(activity.c.id, activity.c.distance_m, effective_duration_expr.label("eff_s"))
            .where(
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
        percentile_rank=percentile_rank, comparable_count=comparable_count, recent=recent
    )


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
        select(activity.c.duration_s).where(
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

    try:
        result = downsample(
            con,
            settings.parquet_dir / stream_row.parquet_path,
            tier=tier,
            channels=requested,
            available_channels=available_channels,
            duration_s=activity_row.duration_s or 0.0,
            n_samples=stream_row.n_samples,
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
