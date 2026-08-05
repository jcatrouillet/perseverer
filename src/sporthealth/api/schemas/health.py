"""Response model for GET /health/observations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthObservationOut(BaseModel):
    metric_key: str
    observed_at_utc: datetime
    local_date: str
    aggregation: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    source: str


# --- Health dashboard (Phase 6): merges the same logical field's several raw metric_key
# namespaces -- see api/routers/health.py::LOGICAL_METRICS and
# docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.


class HealthDashboardDayOut(BaseModel):
    local_date: str
    value_sum: float | None
    value_avg: float | None
    value_min: float | None
    value_max: float | None
    value_last: float | None
    n_observations: int
    # Which of the logical metric's aliases actually supplied this day's row -- transparency
    # for debugging a namespace-cliff, not just a merged number with no provenance.
    source_metric_key: str


class HealthDashboardMetricOut(BaseModel):
    logical_metric: str
    # The most recent local_date with data across ALL aliases, not bounded by the requested
    # date range -- lets the frontend show "last observed: {date}" even when the visible range
    # itself has no data, since garmin_connect's daily sync never touches wellness data (see
    # ADR 0009) and this can otherwise look like live data that silently isn't.
    last_observed: str | None
    daily: list[HealthDashboardDayOut]


class HealthDashboardOut(BaseModel):
    metrics: list[HealthDashboardMetricOut]
