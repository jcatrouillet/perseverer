"""Response model for GET /calendar -- reads day_rollup/health_metric_daily_rollup only, no
DuckDB, no request-time scan (AGENTS.md's rollup mandate). All five stored aggregates are
returned per metric so the caller picks whichever one a given metric needs (sum for steps,
last for resting heart rate, ...) -- see docs/adr/0006-phase-3-read-api-and-rollups.md
decision 1.
"""

from __future__ import annotations

from pydantic import BaseModel


class HealthMetricRollupOut(BaseModel):
    metric_key: str
    value_sum: float | None
    value_avg: float | None
    value_min: float | None
    value_max: float | None
    value_last: float | None
    n_observations: int


class DayRollupOut(BaseModel):
    local_date: str
    activity_count: int
    activity_duration_s: float | None
    activity_moving_duration_s: float | None
    activity_distance_m: float | None
    activity_elevation_gain_m: float | None
    activity_calories: float | None
    sleep_total_s: float | None
    sleep_score: float | None
    health_metrics: list[HealthMetricRollupOut]


class CalendarResponse(BaseModel):
    days: list[DayRollupOut]


# --- Period (week/month) rollups, Phase 6 -- see docs/adr/0009-phase-6-*.md ---


class PeriodHealthMetricRollupOut(BaseModel):
    metric_key: str
    value_sum: float | None
    value_avg: float | None
    value_min: float | None
    value_max: float | None
    value_last: float | None
    n_observations: int


class PeriodRollupOut(BaseModel):
    period_type: str
    period_start: str
    period_end: str
    activity_count: int
    activity_duration_s: float | None
    activity_moving_duration_s: float | None
    activity_distance_m: float | None
    activity_elevation_gain_m: float | None
    activity_calories: float | None
    activity_days_count: int
    sleep_total_s: float | None
    sleep_score: float | None
    health_metrics: list[PeriodHealthMetricRollupOut]


class PeriodCalendarResponse(BaseModel):
    periods: list[PeriodRollupOut]
