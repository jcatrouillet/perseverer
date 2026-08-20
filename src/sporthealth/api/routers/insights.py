"""GET /insights -- reads the `insight` table only, no request-time computation (matches this
codebase's rollup mandate). Refreshed by insights/engine.py on ingest and once daily (see ADR
0012); this endpoint never recomputes anything itself.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, func, select

from sporthealth.api.dependencies import get_conn, require_api_key
from sporthealth.api.schemas.common import to_utc
from sporthealth.api.schemas.insights import ActivityPaceBandsOut, InsightOut, PaceBandOut
from sporthealth.db.schema import activity, activity_metric, insight
from sporthealth.pace_bands import PACE_BANDS

router = APIRouter()


@router.get("/insights")
def list_insights(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    kind: str | None = Query(None),
    window: str | None = Query(None),
) -> list[InsightOut]:
    query = select(insight).where(insight.c.athlete_id == athlete_id)
    if kind is not None:
        query = query.where(insight.c.kind == kind)
    if window is not None:
        query = query.where(insight.c.window == window)
    rows = conn.execute(query.order_by(insight.c.kind, insight.c.window)).fetchall()
    return [
        InsightOut(
            kind=r.kind,
            window=r.window,
            title=r.title,
            detail=json.loads(r.detail),
            value_num=r.value_num,
            metric_key=r.metric_key,
            sport_family=r.sport_family,
            activity_id=r.activity_id,
            local_date=r.local_date,
            computed_at=to_utc(r.computed_at),
        )
        for r in rows
    ]


@router.get("/insights/pace-bands")
def list_pace_bands(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[PaceBandOut]:
    """Total time-in-band across the athlete's whole running history, one row per
    `pace_bands.PACE_BANDS` entry (fast to slow). A plain bounded SUM/GROUP BY over
    already-precomputed `activity_metric` rows (see pace_bands.py::refresh_pace_bands) -- never a
    live stream scan, matching this codebase's rollup mandate."""
    band_metric_keys = [band.metric_key for band in PACE_BANDS]
    rows = conn.execute(
        select(activity_metric.c.metric_key, func.sum(activity_metric.c.value_num))
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key.in_(band_metric_keys),
        )
        .group_by(activity_metric.c.metric_key)
    ).fetchall()
    totals = {metric_key: total for metric_key, total in rows}
    return [
        PaceBandOut(label=band.label, seconds=totals.get(band.metric_key, 0.0))
        for band in PACE_BANDS
    ]


@router.get("/insights/pace-bands/by-activity")
def list_pace_bands_by_activity(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[ActivityPaceBandsOut]:
    """Per-activity time-in-band breakdown -- the same precomputed `activity_metric` rows
    `/insights/pace-bands` sums athlete-wide, here grouped by activity instead of collapsed
    across all of them. Powers the Training bands chart's per-run composition strip (what share
    of *this* run was spent at each pace), one row per running activity that has at least one
    non-zero band. Still a plain bounded query over already-stored rows, never a live stream
    scan."""
    band_metric_keys = [band.metric_key for band in PACE_BANDS]
    rows = conn.execute(
        select(
            activity_metric.c.activity_id,
            activity.c.local_date,
            activity_metric.c.metric_key,
            activity_metric.c.value_num,
        )
        .select_from(activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id))
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key.in_(band_metric_keys),
            activity.c.deleted_at.is_(None),
        )
        .order_by(activity.c.local_date)
    ).fetchall()

    seconds_by_activity: dict[str, dict[str, float]] = {}
    local_date_by_activity: dict[str, str | None] = {}
    for row in rows:
        seconds_by_activity.setdefault(row.activity_id, {})[row.metric_key] = row.value_num
        local_date_by_activity[row.activity_id] = row.local_date

    return [
        ActivityPaceBandsOut(
            activity_id=activity_id,
            local_date=local_date_by_activity[activity_id],
            bands=[
                PaceBandOut(label=band.label, seconds=metrics.get(band.metric_key, 0.0))
                for band in PACE_BANDS
            ],
        )
        for activity_id, metrics in seconds_by_activity.items()
    ]
