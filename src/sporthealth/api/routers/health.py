"""GET /health/observations, GET /health/dashboard."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, func, select

from sporthealth.api.dependencies import get_conn, require_api_key
from sporthealth.api.schemas.common import Page, to_utc
from sporthealth.api.schemas.health import (
    HealthDashboardDayOut,
    HealthDashboardMetricOut,
    HealthDashboardOut,
    HealthObservationOut,
)
from sporthealth.db.schema import health_metric_daily_rollup, health_observation

router = APIRouter()

# One logical field can live under up to three raw metric_key namespaces depending on source
# and era (canonical FIT-derived, Connect-shaped JSON via fit_folder, GDPR export JSON) --
# confirmed against the real ingested database, not assumed from parser docstrings. Order
# matters: the first alias with data for a given day wins (see _merge_logical_metric). Deliberate
# false positives excluded (e.g. garmin.daily_summary.lastSevenDaysAvgRestingHeartRate is a
# distinct trailing-average metric, not an alias of the same daily value). See
# docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
LOGICAL_METRICS: dict[str, list[str]] = {
    "steps": ["garmin.daily_summary.totalSteps", "garmin.export.UDSFile.totalSteps"],
    "calories": [
        "garmin.daily_summary.totalKilocalories",
        "garmin.export.UDSFile.totalKilocalories",
    ],
    "resting_heart_rate": [
        "resting_heart_rate",
        "garmin.daily_summary.restingHeartRate",
        "garmin.export.UDSFile.restingHeartRate",
    ],
    "max_heart_rate": ["garmin.daily_summary.maxHeartRate", "garmin.export.UDSFile.maxHeartRate"],
    "floors_ascended": [
        "garmin.daily_summary.floorsAscendedInMeters",
        "garmin.export.UDSFile.floorsAscendedInMeters",
    ],
    "vo2max": [
        "garmin.export.MetricsMaxMetData.vo2MaxValue",
        "fit.max_met_data.vo2_max",
        "garmin.export.ActivityVo2Max.vo2MaxValue",
    ],
    "hrv_nightly_average": [
        "hrv.last_night_average",
        "garmin.export.TrainingReadinessDTO.hrvWeeklyAverage",
    ],
    "spo2_average": [
        "garmin.daily_summary.averageSpo2",
        "garmin.export.UDSFile.averageSpo2Value",
    ],
    "stress_average": [
        "garmin.daily_summary.averageStressLevel",
        "garmin.export.UDSFile.allDayStress",
    ],
}


@router.get("/health/observations")
def list_health_observations(
    athlete_id: Annotated[str, Depends(require_api_key)],
    metric_key: list[str] = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
) -> Page[HealthObservationOut]:
    query = select(health_observation).where(
        health_observation.c.athlete_id == athlete_id,
        health_observation.c.metric_key.in_(metric_key),
        health_observation.c.local_date >= start_date.isoformat(),
        health_observation.c.local_date <= end_date.isoformat(),
    )
    total = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = conn.execute(
        query.order_by(health_observation.c.observed_at_utc).limit(limit).offset(offset)
    ).fetchall()

    items = [
        HealthObservationOut(
            metric_key=r.metric_key,
            observed_at_utc=to_utc(r.observed_at_utc),
            local_date=r.local_date,
            aggregation=r.aggregation,
            value_num=r.value_num,
            value_text=r.value_text,
            unit=r.unit,
            source=r.source,
        )
        for r in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


def _merge_logical_metric(
    conn: Connection, *, athlete_id: str, aliases: list[str], start_date: date, end_date: date
) -> HealthDashboardMetricOut | None:
    rows = conn.execute(
        select(health_metric_daily_rollup).where(
            health_metric_daily_rollup.c.athlete_id == athlete_id,
            health_metric_daily_rollup.c.metric_key.in_(aliases),
            health_metric_daily_rollup.c.local_date >= start_date.isoformat(),
            health_metric_daily_rollup.c.local_date <= end_date.isoformat(),
        )
    ).fetchall()

    # First alias (in priority order) with a row for that date wins -- a namespace switchover
    # (e.g. export-backfill era vs. live-sync era) never produces two rows for the same day.
    by_date: dict[str, HealthDashboardDayOut] = {}
    for r in rows:
        existing = by_date.get(r.local_date)
        if existing is not None and aliases.index(existing.source_metric_key) <= aliases.index(
            r.metric_key
        ):
            continue
        by_date[r.local_date] = HealthDashboardDayOut(
            local_date=r.local_date,
            value_sum=r.value_sum,
            value_avg=r.value_avg,
            value_min=r.value_min,
            value_max=r.value_max,
            value_last=r.value_last,
            n_observations=r.n_observations,
            source_metric_key=r.metric_key,
        )

    last_observed = conn.execute(
        select(func.max(health_metric_daily_rollup.c.local_date)).where(
            health_metric_daily_rollup.c.athlete_id == athlete_id,
            health_metric_daily_rollup.c.metric_key.in_(aliases),
        )
    ).scalar_one_or_none()

    if not by_date and last_observed is None:
        return None

    daily = sorted(by_date.values(), key=lambda d: d.local_date)
    return HealthDashboardMetricOut(
        logical_metric="",  # filled in by the caller, which knows the dict key
        last_observed=last_observed,
        daily=daily,
    )


@router.get("/health/dashboard")
def get_health_dashboard(
    athlete_id: Annotated[str, Depends(require_api_key)],
    start_date: date = Query(...),
    end_date: date = Query(...),
    conn: Connection = Depends(get_conn),
) -> HealthDashboardOut:
    metrics = []
    for logical_metric, aliases in LOGICAL_METRICS.items():
        merged = _merge_logical_metric(
            conn, athlete_id=athlete_id, aliases=aliases, start_date=start_date, end_date=end_date
        )
        if merged is not None:
            metrics.append(merged.model_copy(update={"logical_metric": logical_metric}))
    return HealthDashboardOut(metrics=metrics)
