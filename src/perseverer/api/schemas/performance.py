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
    aerobic_threshold_pace_s_per_km: float | None
    aerobic_threshold_hr_bpm: float | None
    aerobic_threshold_hr_source: str | None
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


class ActivityRefOut(BaseModel):
    activity_id: str
    local_date: str
    name: str | None
    sport: str
    distance_m: float | None
    duration_s: float | None


class ThresholdHrContributorOut(ActivityRefOut):
    pace_s_per_km: float
    avg_hr_bpm: float
    # True for the run(s) whose own avg_hr_bpm defines the empirical median (one run when the
    # qualifying count is odd, two when it's even and the median averages them).
    is_median: bool


class ThresholdHrBreakdownOut(BaseModel):
    threshold_hr_bpm: float | None
    threshold_hr_source: str | None
    reference_pace_s_per_km: float | None
    # Every qualifying run near reference_pace_s_per_km in the trailing window, sorted by
    # avg_hr_bpm ascending -- populated when threshold_hr_source == "empirical"; empty otherwise.
    contributors: list[ThresholdHrContributorOut]
    # The activity that set max_hr_bpm (only when threshold_hr_source == "fallback" and the max
    # HR itself came from a real observation, not the Tanaka formula -- a formula has no activity
    # behind it).
    max_hr_driving_activity: ActivityRefOut | None
    missing: list[str]


class ThresholdFactorAnalysisOut(BaseModel):
    as_of: str
    # The same VO2max factor analysis GET /performance/vo2max-analysis returns -- both threshold
    # paces below are pure functions of this same rolling_vdot, so "which workout led to the
    # current threshold pace" is exactly "which workout is driving VO2max," not a second answer.
    vo2max: Vo2maxFactorAnalysisOut
    anaerobic_threshold_pace_s_per_km: float | None
    aerobic_threshold_pace_s_per_km: float | None
    anaerobic_threshold_hr: ThresholdHrBreakdownOut
    aerobic_threshold_hr: ThresholdHrBreakdownOut
    max_hr_bpm: float | None
    max_hr_source: str | None
