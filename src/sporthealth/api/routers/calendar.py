"""GET /calendar -- reads day_rollup/health_metric_daily_rollup only, no DuckDB, no
request-time scan (CLAUDE.md's rollup mandate). See
docs/adr/0006-phase-3-read-api-and-rollups.md.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from sporthealth.api.dependencies import get_conn, require_api_key
from sporthealth.api.schemas.calendar import CalendarResponse, DayRollupOut, HealthMetricRollupOut
from sporthealth.db.schema import day_rollup, health_metric_daily_rollup
from sporthealth.db.seed import DEFAULT_ATHLETE_ID

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/calendar")
def get_calendar(
    start_date: date = Query(...),
    end_date: date = Query(...),
    metric_keys: list[str] | None = Query(None),
    conn: Connection = Depends(get_conn),
) -> CalendarResponse:
    day_rows = conn.execute(
        select(day_rollup)
        .where(
            day_rollup.c.athlete_id == DEFAULT_ATHLETE_ID,
            day_rollup.c.local_date >= start_date.isoformat(),
            day_rollup.c.local_date <= end_date.isoformat(),
        )
        .order_by(day_rollup.c.local_date)
    ).fetchall()

    health_query = select(health_metric_daily_rollup).where(
        health_metric_daily_rollup.c.athlete_id == DEFAULT_ATHLETE_ID,
        health_metric_daily_rollup.c.local_date >= start_date.isoformat(),
        health_metric_daily_rollup.c.local_date <= end_date.isoformat(),
    )
    if metric_keys:
        health_query = health_query.where(
            health_metric_daily_rollup.c.metric_key.in_(metric_keys)
        )
    health_rows = conn.execute(health_query).fetchall()

    by_date: dict[str, list[HealthMetricRollupOut]] = defaultdict(list)
    for r in health_rows:
        by_date[r.local_date].append(
            HealthMetricRollupOut(
                metric_key=r.metric_key,
                value_sum=r.value_sum,
                value_avg=r.value_avg,
                value_min=r.value_min,
                value_max=r.value_max,
                value_last=r.value_last,
                n_observations=r.n_observations,
            )
        )

    days = [
        DayRollupOut(
            local_date=r.local_date,
            activity_count=r.activity_count,
            activity_duration_s=r.activity_duration_s,
            activity_moving_duration_s=r.activity_moving_duration_s,
            activity_distance_m=r.activity_distance_m,
            activity_elevation_gain_m=r.activity_elevation_gain_m,
            activity_calories=r.activity_calories,
            sleep_total_s=r.sleep_total_s,
            sleep_score=r.sleep_score,
            health_metrics=by_date.get(r.local_date, []),
        )
        for r in day_rows
    ]
    return CalendarResponse(days=days)
