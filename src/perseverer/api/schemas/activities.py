"""Response models for GET /activities and GET /activities/{id}."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DeviceOut(BaseModel):
    manufacturer: str | None
    product: str | None
    serial_number: str | None


class LapOut(BaseModel):
    lap_index: int
    start_time_utc: datetime
    duration_s: float | None
    moving_duration_s: float | None
    distance_m: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_speed_mps: float | None


class SplitOut(BaseModel):
    split_index: int
    split_type: str | None
    start_time_utc: datetime | None
    end_time_utc: datetime | None
    duration_s: float | None
    distance_m: float | None
    # Bouldering only, only ever set on a "climb_active" split -- see fit/parser.py's own
    # comment for how these two undocumented FIT fields were reverse-engineered.
    climb_grade: int | None
    climb_result: str | None
    # Also bouldering only, but set on both "climb_active" and "climb_rest" splits.
    climb_avg_hr: float | None
    climb_max_hr: float | None
    # True only for a route the athlete added by hand (bouldering_overrides.py::add_manual_route)
    # -- never set for a FIT-derived row. Lets the frontend offer a delete affordance only where
    # it's actually safe (see split.is_manual's own comment in db/schema.py).
    is_manual: bool | None


class RouteOut(BaseModel):
    encoded_polyline: str | None
    min_lat: float | None
    min_lng: float | None
    max_lat: float | None
    max_lng: float | None
    start_lat: float | None
    start_lng: float | None
    end_lat: float | None
    end_lng: float | None


class ActivityMetricOut(BaseModel):
    metric_key: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    source: str


class TransportMixFlagOut(BaseModel):
    at_start: bool
    at_end: bool
    suggested_trim_start_s: float | None
    suggested_trim_end_s: float | None


class ActivitySummary(BaseModel):
    id: str
    start_time_utc: datetime
    utc_offset_s: int
    local_date: str | None
    sport: str
    sub_sport: str | None
    name: str | None
    # True/False when Garmin Connect's own event-type classification is known for this activity
    # (see garmin_activity_summary.py's own docstring); None when never matched against a
    # garmin_export summarizedActivities entry, meaning genuinely unknown rather than
    # "confirmed not a race".
    is_race: bool | None
    duration_s: float | None
    moving_duration_s: float | None
    distance_m: float | None
    elevation_gain_m: float | None
    calories: float | None
    avg_hr_bpm: float | None
    max_hr_bpm: float | None
    training_load: float | None
    # Borg CR10 effort rating (0-10, half-point resolution): fit.session.workout_rpe's raw FIT
    # value is the same scale x10 (see routers/activities.py's read of this field for the
    # profile.py introspection that confirmed this) -- this is the descaled, presentation value.
    workout_rpe: float | None
    # The athlete's recorded body weight at the time of this specific activity
    # (fit.user_profile.weight, a generic per-session field -- present on ~99% of activities,
    # confirmed against the real archive). Exposed so the frontend can derive MET-minutes
    # (calories / weight_kg, a standard gross-MET approximation) without a second per-activity
    # fetch -- not itself a metric anyone reads directly.
    weight_kg: float | None
    # Daniels-Gilbert running performance index, GAP-adjusted when a distance/altitude stream is
    # available -- see performance.py's own docstring. Null for non-running activities, or a
    # running activity too short/anomalous for the underlying aerobic model (see vdot.py).
    vdot: float | None
    # Garmin Connect's own structured "Workout Builder" name (e.g. "W11 Tue - 4x2km Threshold"),
    # from activity_workout.name -- the FIT file's own activity.name is usually just the generic
    # device default ("Running") when a pre-planned workout was followed, so the frontend falls
    # back to this before showing no title at all (see yearStats.ts::displayActivityName). None
    # for the (large majority of) activities with no such plan.
    workout_name: str | None
    primary_source: str
    stream_available: bool
    # Bouldering only, all three None for any other activity -- computed from this activity's
    # own `split` rows (climb_route_count: how many climb_active rows have a grade at all;
    # climb_max_completed_grade: the highest grade with result="completed"; climb_time_s: total
    # duration_s summed across climb_active rows, i.e. time actually climbing, excluding rest).
    # Exposed here (not just on the detail page's full `splits`) so the activity-card/day-view
    # pill can show them without a second per-activity fetch.
    climb_route_count: int | None
    climb_max_completed_grade: int | None
    climb_time_s: float | None


class ActivityDetail(ActivitySummary):
    device: DeviceOut | None
    laps: list[LapOut]
    splits: list[SplitOut]
    route: RouteOut | None
    metrics: list[ActivityMetricOut]
    # Not a stored per-activity field -- Garmin logs this to the athlete's daily hydration log,
    # not to the activity itself, so it's matched by nearest timestamp at read time (see
    # routers/activities.py::_estimated_sweat_loss_ml for the real-data verification that
    # justified this). None whenever no hydration-log entry lands close enough after the
    # activity to be confidently this one's -- never a guessed number.
    estimated_sweat_loss_ml: float | None
    # The athlete's own logged fueling intake during the activity -- no vendor source carries
    # this (see sport_override.py's own docstring), so both are None until entered via
    # PATCH .../fueling.
    carbohydrates_g: float | None
    sodium_mg: float | None
    # Computed on-demand from this activity's own Parquet stream (see transport_mix.py) --
    # None whenever the sport isn't hiking/walking or no sustained fast segment touches either
    # boundary. Detail-page-only (not on ActivitySummary): scanning every activity's own Parquet
    # file for a list view would violate this app's precomputed-rollups discipline (CLAUDE.md).
    transport_mix_flag: TransportMixFlagOut | None
    # True once an activity_trim_override row exists for this activity -- lets the frontend show
    # "Adjust trim"/"Undo trim" instead of the initial flag banner.
    has_trim: bool


class ActivityContextRecentOut(BaseModel):
    id: str
    local_date: str | None
    distance_m: float
    # Moving-preferred effective duration (moving_duration_s, falling back to duration_s) --
    # same convention as the frontend's effectiveDurationS(), computed once here rather than
    # exposing both raw fields and making every consumer re-derive it.
    duration_s: float
    # Only populated for `fastest` (the compact per-row readout on the activity detail page's
    # "Fastest for this distance" table) -- `recent`'s sparkline strip has no per-point HR
    # readout, so its rows leave this None rather than pay for a subquery nothing reads.
    avg_hr_bpm: float | None = None


class ActivityContextOut(BaseModel):
    # None when there are no other same-sport, similar-distance activities to compare against
    # (comparable_count == 0) -- never a fabricated number for a first-of-its-kind effort.
    percentile_rank: float | None
    comparable_count: int
    recent: list[ActivityContextRecentOut]
    # The fastest (by effective pace) up-to-30 same-sport activities whose distance falls in the
    # same whole-kilometre bucket as this one (floor(distance_m / 1000) -- a 26.29km run only
    # compares against [26000, 27000)m, not the wider +/-15% band `percentile_rank` uses).
    # Reuses ActivityContextRecentOut's shape (id/local_date/distance_m/duration_s) rather than a
    # new type, since it's the same "one row per comparable activity" shape. Includes this
    # activity itself when it belongs in the top 30, unlike `recent`'s 90-day-window framing
    # which has no such exclusion either.
    fastest: list[ActivityContextRecentOut]


class ActivityComparisonRowOut(BaseModel):
    id: str
    local_date: str | None
    distance_m: float
    # Moving-preferred effective duration, same convention as ActivityContextRecentOut.duration_s.
    duration_s: float
    vdot: float | None
    # m/s, SI storage convention (CLAUDE.md principle 6) -- see gap.py. The presentation layer
    # converts to a min/km "grade adjusted pace" the same way it already does for plain pace.
    avg_gap_speed_mps: float | None
    avg_hr_bpm: float | None
    # Already doubled to strides/min -- see routers/activities.py::get_activity_comparisons for
    # why (FIT's own avg_running_cadence field is a single-foot rate).
    avg_cadence_spm: float | None


class ActivityComparisonsOut(BaseModel):
    # Echoed back so the frontend can phrase its own "within Nm of this run's start, +/-X%
    # distance" caption from the same numbers the query actually used, rather than a second
    # hardcoded copy of these thresholds.
    start_radius_m: float
    distance_band_fraction: float
    # How many activities actually matched before capping to the 10 most recent -- lets the
    # frontend say "10 of 34 matching runs" rather than implying 10 is the whole story.
    matched_count: int
    rows: list[ActivityComparisonRowOut]


class ClimbComparisonRowOut(BaseModel):
    id: str
    local_date: str | None
    duration_s: float
    route_count: int
    max_completed_grade: int | None
    climb_time_s: float | None


class ClimbComparisonsOut(BaseModel):
    duration_band_fraction: float
    matched_count: int
    rows: list[ClimbComparisonRowOut]


class ClimbGradeBreakdownOut(BaseModel):
    grade: int
    # result == "attempt", or an unconfirmed "unknown_<n>" raw value (see fit/parser.py) --
    # counted conservatively as an attempt rather than assumed completed.
    attempted: int
    completed: int


class ClimbingSummaryOut(BaseModel):
    session_count: int
    total_climb_time_s: float
    total_routes: int
    max_completed_grade: int | None
    grade_breakdown: list[ClimbGradeBreakdownOut]


class ActivityWeatherOut(BaseModel):
    # False whenever there's nothing to show -- no GPS start point to query against, or the
    # Open-Meteo fetch failed/returned no usable data for this activity's time window. Never a
    # fabricated range (CLAUDE.md's raw-first rule): every non-null field below came from a real
    # archived Open-Meteo response.
    available: bool
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    humidity_min_pct: float | None = None
    humidity_max_pct: float | None = None
    # WMO weather code (https://open-meteo.com/en/docs -- the same taxonomy the archive API
    # returns) at the hour closest to the activity's own start. Icon mapping is a frontend
    # presentation concern, not modeled here.
    weather_code: int | None = None
    # The three fields below are single values at the hour closest to the activity's own start
    # (not a min/max range like temperature/humidity above) -- see weather.py's own module
    # docstring for why. Independently None (never fabricated) whenever Open-Meteo's response
    # didn't have that particular reading, even when the fields above are present.
    feels_like_c: float | None = None
    wind_speed_mps: float | None = None
    wind_direction_deg: float | None = None


class ActivityLocationOut(BaseModel):
    # False whenever there's nothing to show -- no GPS start point to query against, or the
    # Nominatim fetch failed/returned no usable place name. Never a fabricated name.
    available: bool
    location_name: str | None = None


class ActivityWorkoutStepOut(BaseModel):
    step_index: int
    duration_type: str | None
    duration_time_s: float | None
    duration_distance_m: float | None
    target_type: str | None
    # Only populated for target_type == "speed" -- see ParsedWorkoutStep's own docstring
    # (fit/types.py) for why other target types aren't extracted into a range yet.
    target_low_mps: float | None
    target_high_mps: float | None
    intensity: str | None
    # Only populated when duration_type == "repeat_until_steps_cmplt": "repeat steps
    # [repeat_from_step..step_index-1] repeat_count times". Unexpanded, per "raw first" -- see
    # fit/parser.py::_parse_workout's own docstring; expansion (e.g. to align with recorded
    # laps, one per executed step) is a presentation concern done by the caller.
    repeat_from_step: int | None
    repeat_count: int | None


class ActivityWorkoutOut(BaseModel):
    # None whenever this activity has no recorded workout plan (most don't) -- not an empty
    # ActivityWorkoutOut, so the frontend can distinguish "no plan" from "plan with 0 steps".
    name: str | None
    description: str | None
    steps: list[ActivityWorkoutStepOut]


class ActivityMapPointOut(BaseModel):
    id: str
    local_date: str | None
    sport: str
    name: str | None
    distance_m: float | None
    start_lat: float
    start_lng: float


class ActivityRouteOut(BaseModel):
    id: str
    # simplified_polyline (not encoded_polyline): a batch response for a page of thumbnail-sized
    # maps should stay small, and simplified_polyline is exactly the "cheaper to render" variant
    # route_geom already carries (currently identical bytes to encoded_polyline -- see ADR 0002 --
    # but this is the field that would shrink if real Douglas-Peucker simplification lands later,
    # so callers that only need a thumbnail shouldn't have to change).
    simplified_polyline: str | None


class ActivitySourceOut(BaseModel):
    """One `activity_source_link` row -- a source this activity's data actually came from,
    inspectable per the Phase 8 acceptance criterion ("both sources inspectable"). `link_id`
    is that row's own id, passed back into POST .../sources/{link_id}/split to undo a wrong
    merge."""

    link_id: int
    source: str
    external_id: str
    ingested_at: datetime
    # False for the one link a split can't act on -- an activity with only one source has
    # nothing to split off (see the 400 the split endpoint returns in that case).
    can_split: bool


class ActivityMergeDecisionOut(BaseModel):
    """One `merge_decision` row where a candidate matched into this activity -- the "why" a
    source ended up linked here, for the same "both sources inspectable" acceptance criterion."""

    candidate_ref: str
    reasons: list[str]
    decided_at: datetime


class ActivitySourcesOut(BaseModel):
    sources: list[ActivitySourceOut]
    merge_decisions: list[ActivityMergeDecisionOut]


class ActivitySplitOut(BaseModel):
    new_activity_id: str


class ActivitySportOverrideIn(BaseModel):
    # Free-form, matching activity.sport's own column (no fixed enum -- "additive schema
    # evolution": a new sport needs zero code changes, here included, same as everywhere else).
    sport: str
    sub_sport: str | None = None


class ActivitySportOverrideOut(BaseModel):
    sport: str
    sub_sport: str | None


class ActivityRaceOverrideIn(BaseModel):
    is_race: bool


class ActivityRaceOverrideOut(BaseModel):
    is_race: bool


class ActivityNameOverrideIn(BaseModel):
    name: str


class ActivityNameOverrideOut(BaseModel):
    name: str


class ActivityFuelingIn(BaseModel):
    carbohydrates_g: float | None = None
    sodium_mg: float | None = None


class ActivityFuelingOut(BaseModel):
    carbohydrates_g: float | None
    sodium_mg: float | None


class ActivityTrimIn(BaseModel):
    # Elapsed seconds from the activity's own recorded start. Either may be omitted (None) to
    # leave that side untrimmed -- see activity_trim.py::set_activity_trim.
    trim_start_s: float | None = None
    trim_end_s: float | None = None


class ClimbRouteStatusIn(BaseModel):
    # Both optional -- send just one to correct only that field, leaving any separately-recorded
    # correction on this same route untouched (see bouldering_overrides.py's own docstring).
    # `result` matches bouldering_overrides.py::VALID_RESULTS exactly -- validated there, not
    # re-declared as a Literal here, so the two can't quietly drift apart.
    result: str | None = None
    grade: int | None = None


class ClimbRouteAddIn(BaseModel):
    grade: int
    result: str
