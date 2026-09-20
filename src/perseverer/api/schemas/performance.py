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


class ZoneRunSampleOut(ActivityRefOut):
    pace_s_per_km: float
    avg_hr_bpm: float


class PaceHrZoneOut(BaseModel):
    number: int
    label: str
    description: str
    pace_fast_s_per_km: float | None
    pace_slow_s_per_km: float | None
    hr_low_bpm: int | None
    hr_high_bpm: int | None
    hr_source: str | None  # "empirical" | "formula_fallback" | None
    qualifying_run_count: int
    sample_runs: list[ZoneRunSampleOut]


class PaceHrZonesOut(BaseModel):
    as_of: str
    # The single best (highest) VDOT/max HR within their own trailing lookback window (races get
    # RACE_WINDOW_DAYS, training runs and max HR get their own shorter/matching windows -- see
    # pace_hr_zones.py's own "How far back" docstring section), not a rolling-window "current
    # fitness" value like performance_daily_rollup's own 42-day rolling_vdot.
    # `profile_vdot_activity` is the one run that set it (a maximum, never averaged); max HR has no
    # such activity when it's the Tanaka-formula fallback.
    profile_vdot: float | None
    profile_vdot_activity: ActivityRefOut | None
    profile_vdot_source: str | None  # "race" | "training_run" | None
    profile_max_hr_bpm: float | None
    profile_max_hr_source: str | None  # "empirical" | "formula_fallback" | None
    zones: list[PaceHrZoneOut]
    missing: list[str]


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
    # VDOT-based model pace_hr_zones.py also builds on) -- shown alongside the curve as
    # reference lines, never blended into it. Only the pair relevant to `metric` is ever
    # non-null; the rest are always null, not just "unpopulated for this response."
    threshold_pace_s_per_km: float | None = None
    aerobic_threshold_pace_s_per_km: float | None = None
    threshold_hr_bpm: float | None = None
    aerobic_threshold_hr_bpm: float | None = None
    max_hr_bpm: float | None = None
