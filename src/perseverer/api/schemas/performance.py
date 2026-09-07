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
    threshold_pace_s_per_km: float | None
    threshold_hr_bpm: float | None
    threshold_hr_source: str | None
    predicted_5k_s: float | None
    predicted_10k_s: float | None
    predicted_half_marathon_s: float | None
    predicted_marathon_s: float | None
