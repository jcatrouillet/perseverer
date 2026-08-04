"""GET /activities, GET /activities/{id}, GET /activities/{id}/stream."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, func, select

from sporthealth.api.dependencies import get_conn, get_duckdb, require_api_key
from sporthealth.api.schemas.activities import (
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
    query = select(
        activity.c.id,
        activity.c.start_time_utc,
        activity.c.local_date,
        activity.c.sport,
        activity.c.sub_sport,
        activity.c.name,
        activity.c.duration_s,
        activity.c.distance_m,
        activity.c.elevation_gain_m,
        activity.c.calories,
        activity.c.primary_source,
        stream_exists.label("stream_available"),
    ).where(activity.c.athlete_id == athlete_id, activity.c.deleted_at.is_(None))

    if start_date is not None:
        query = query.where(activity.c.local_date >= start_date.isoformat())
    if end_date is not None:
        query = query.where(activity.c.local_date <= end_date.isoformat())
    if sport is not None:
        query = query.where(activity.c.sport == sport)

    total = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = conn.execute(
        query.order_by(activity.c.start_time_utc.desc()).limit(limit).offset(offset)
    ).fetchall()

    items = [
        ActivitySummary(
            id=r.id,
            start_time_utc=to_utc(r.start_time_utc),
            local_date=r.local_date,
            sport=r.sport,
            sub_sport=r.sub_sport,
            name=r.name,
            duration_s=r.duration_s,
            distance_m=r.distance_m,
            elevation_gain_m=r.elevation_gain_m,
            calories=r.calories,
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

    return ActivityDetail(
        id=row.id,
        start_time_utc=to_utc(row.start_time_utc),
        local_date=row.local_date,
        sport=row.sport,
        sub_sport=row.sub_sport,
        name=row.name,
        duration_s=row.duration_s,
        distance_m=row.distance_m,
        elevation_gain_m=row.elevation_gain_m,
        calories=row.calories,
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
