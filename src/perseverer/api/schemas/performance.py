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


class RaceReadinessPointOut(BaseModel):
    as_of: str
    weekly_distance_compliance_pct: float
    long_run_compliance_pct: float
    readiness_pct: float


class RaceReadinessWeekOut(BaseModel):
    week_start: str
    distance_m: float


class RaceReadinessOut(BaseModel):
    # False -- never a fabricated readiness -- when the athlete has no upcoming running race on
    # the calendar (or the given race_id doesn't belong to them). See race_readiness.py's own
    # module docstring for the full reasoning behind every field below.
    available: bool
    race_id: int | None = None
    race_name: str | None = None
    race_local_date: str | None = None
    race_distance_m: float | None = None
    weekly_distance_target_m: float | None = None
    long_run_target_m: float | None = None
    # This request's own resolved "now" -- the same value `current.as_of` carries, included at
    # the top level too since it's the one field a caller reaches for immediately.
    as_of: str | None = None
    current: RaceReadinessPointOut | None = None
    # The independently-computed VDOT-based prediction for this race's own distance (the same
    # value `planned_race.predicted_duration_s` already surfaces) -- shown alongside readiness,
    # never blended into it. `None` for a non-standard distance, same as that existing field.
    predicted_duration_s: float | None = None
    # One point per week over the weekly-distance window (182 days) -- "the evolution of this
    # readiness over time."
    history: list[RaceReadinessPointOut] = []
    # The actual realized numbers behind weekly_distance_compliance_pct/long_run_compliance_pct
    # above -- one entry per Monday-start week (0.0 never omitted), for a dedicated chart to plot
    # real bars against a target reference line, distinct from history's already-weighted/
    # combined percentages.
    weekly_distance_series: list[RaceReadinessWeekOut] = []
    long_run_series: list[RaceReadinessWeekOut] = []


class PerformanceCurvePointOut(BaseModel):
    duration_s: int
    value: float
    # The activity that actually set this bucket's record -- same "driving activity" provenance
    # instinct Vo2maxContributorOut already establishes.
    activity_id: str
    local_date: str


class PerformanceCurveOut(BaseModel):
    # False -- never a fabricated curve -- when no activity in the requested range/sport
    # selection has the stream channel(s) this metric needs at all.
    available: bool
    metric: str  # "pace" | "gap" | "heart_rate"
    points: list[PerformanceCurvePointOut] = []
    # The athlete's own already-computed threshold pace/HR (performance_daily_rollup, the same
    # VDOT-based model threshold_analysis.py already surfaces) -- shown alongside the curve as
    # reference lines, never blended into it. Only the pair relevant to `metric` is ever
    # non-null; the rest are always null, not just "unpopulated for this response."
    threshold_pace_s_per_km: float | None = None
    aerobic_threshold_pace_s_per_km: float | None = None
    threshold_hr_bpm: float | None = None
    aerobic_threshold_hr_bpm: float | None = None
    max_hr_bpm: float | None = None
