"""Response model for GET /performance -- the independently-computed race-time predictions and
max HR/threshold pace/threshold HR series. See performance_rollup.py's own docstring for the
model and why it deliberately never uses Garmin's own precomputed equivalents.
"""

from __future__ import annotations

from pydantic import BaseModel


class PerformanceDailyRollupOut(BaseModel):
    local_date: str
    rolling_vdot: float | None
    max_hr_bpm: float | None
    max_hr_source: str | None
    threshold_pace_s_per_km: float | None
    threshold_hr_bpm: float | None
    threshold_hr_source: str | None
    predicted_5k_s: float | None
    predicted_10k_s: float | None
    predicted_half_marathon_s: float | None
    predicted_marathon_s: float | None


class Vo2maxContributorOut(BaseModel):
    activity_id: str
    local_date: str
    name: str | None
    sport: str
    distance_m: float | None
    duration_s: float | None
    vdot: float


class Vo2maxFactorAnalysisOut(BaseModel):
    as_of: str
    window_start: str
    window_end: str
    rolling_vdot: float | None
    driving_activity: Vo2maxContributorOut | None
    other_contributors: list[Vo2maxContributorOut]
    expires_on: str | None
    days_since_last_qualifying_run: int | None
    missing: list[str]
