"""Response models for GET /activities and GET /activities/{id}.

`LapOut.avg_gap_speed_mps` is computed fresh per request (`gap.py::compute_lap_gap_speeds_mps`,
`activities.py::get_activity`'s own wiring) from the activity's raw Parquet stream, not stored --
same "detail-page-only, computed every request" precedent `transport_mix_flag` already uses on
this same endpoint. `None` for a non-running activity, an activity with no stream, or a lap whose
own slice of the stream is too short/missing a channel.
"""

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
    # m/s, SI storage convention (AGENTS.md principle 6) -- see module docstring above and
    # gap.py::compute_lap_gap_speeds_mps.
    avg_gap_speed_mps: float | None


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
    # A Kaya-sourced route's label (Kaya's own name, else "<hold colour> - <wall>"); null for a
    # Garmin-recorded route. See kaya_ingest.py.
    climb_name: str | None = None
    # "kaya" for a route imported from Kaya (kaya_ingest.py); null for a Garmin/FIT-derived or
    # manually-added row. Lets the UI hide per-route duration/HR, which Kaya rows never carry.
    source: str | None = None
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


class DuplicateCandidateOut(BaseModel):
    # Another activity find_duplicate_candidates (activity_merge.py) considers the same
    # real-world activity as this one -- enough for the frontend's "possibly the same as X"
    # banner without a second fetch.
    id: str
    name: str | None
    primary_source: str
    start_time_utc: datetime
    distance_m: float | None
    duration_s: float | None


class DuplicatePairOut(BaseModel):
    # One relationship from find_all_duplicate_pairs (activity_merge.py) -- the Settings page's
    # list-wide duplicate scan. Both sides use the same shape as DuplicateCandidateOut so the
    # frontend can link either one straight to its own activity detail page's merge tool.
    activity_a: DuplicateCandidateOut
    activity_b: DuplicateCandidateOut


class TrimCandidateOut(BaseModel):
    # One activity transport_mix.detect_transport_mix flagged, from the Settings page's
    # list-wide scan -- everything the detail-page's own TransportMixFlagOut carries, plus
    # enough activity identity for the Settings list to link straight to it.
    id: str
    name: str | None
    sport: str
    start_time_utc: datetime
    distance_m: float | None
    duration_s: float | None
    flag: TransportMixFlagOut


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
    # Peak altitude reached, not cumulative ascent (elevation_gain_m above) -- see
    # db/schema.py::activity.max_altitude_m's own comment. None when the activity has no
    # altitude stream data at all.
    max_altitude_m: float | None
    calories: float | None
    avg_hr_bpm: float | None
    max_hr_bpm: float | None
    # Prefers the athlete's own pace-calibrated running_tss (running_load.py) over Garmin's
    # uncalibrated fit.session.training_load_peak wherever one exists -- see
    # routers/activities.py::TRAINING_LOAD_METRIC_KEYS. Matches what fitness_daily_rollup's own
    # CTL/ATL/TSB aggregate already uses per activity, so this and GET /fitness never disagree.
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
    # file for a list view would violate this app's precomputed-rollups discipline (AGENTS.md).
    transport_mix_flag: TransportMixFlagOut | None
    # True once an activity_trim_override row exists for this activity -- lets the frontend show
    # "Adjust trim"/"Undo trim" instead of the initial flag banner.
    has_trim: bool
    # Other activities that look like the same real-world activity as this one (see
    # activity_merge.py::find_duplicate_candidates) -- computed fresh per request the same way
    # transport_mix_flag is, bounded to this one activity's own +/-1 day window, never a
    # list-wide scan. Usually empty.
    duplicate_candidates: list[DuplicateCandidateOut]


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
    # m/s, SI storage convention (AGENTS.md principle 6) -- see gap.py. The presentation layer
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


class ActivityWeatherHourlyPointOut(BaseModel):
    """One hourly bucket of the activity's own window -- the field that lets a consumer (an AI
    coaching agent, this project's own frontend) render a full run-window conditions table
    without a second call to Open-Meteo. Re-derived from the archived raw response on every
    request (weather.py::parse_open_meteo_hourly_series) rather than stored -- see weather.py's
    own module docstring. Every field below is independently None (never fabricated) whenever
    Open-Meteo's response lacks that array entirely (an old archive predating dew_point_2m/
    shortwave_radiation/cloud_cover) or that one hour's reading."""

    time_utc: datetime
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    dew_point_c: float | None = None
    relative_humidity_pct: float | None = None
    shortwave_radiation_wm2: float | None = None
    cloud_cover_pct: float | None = None
    wind_speed_mps: float | None = None
    wind_direction_deg: float | None = None
    precipitation_mm: float | None = None


class ActivityWeatherOut(BaseModel):
    # False whenever there's nothing to show -- no GPS start point to query against, or the
    # Open-Meteo fetch failed/returned no usable data for this activity's time window. Never a
    # fabricated range (AGENTS.md's raw-first rule): every non-null field below came from a real
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
    # Everything below was added for a materially different consumer than the fields above: an
    # AI coaching agent judging heat stress in bpm/pace terms, not just quoting numbers -- see
    # weather.py's own module docstring for exactly why each field matters and how it's derived.
    # All are min/max window aggregates (same convention as temperature/humidity above) unless
    # noted, and all are independently None (never fabricated) whenever Open-Meteo's response
    # lacks that array entirely -- including for every activity whose weather was cached before
    # these fields existed, until a backfill re-fetches it (weather_backfill.py).
    dew_point_min_c: float | None = None
    dew_point_max_c: float | None = None
    solar_radiation_max_wm2: float | None = None
    solar_radiation_mean_wm2: float | None = None
    cloud_cover_min_pct: float | None = None
    cloud_cover_max_pct: float | None = None
    apparent_temperature_min_c: float | None = None
    apparent_temperature_max_c: float | None = None
    # A window SUM, not a min/max range -- "how much rain fell during the run," see weather.py's
    # own module docstring. 0.0 is a real reading (no rain); None means Open-Meteo's response has
    # no usable precipitation data for this window at all, including every activity cached before
    # this field existed, until a backfill re-fetches it.
    precipitation_mm: float | None = None
    # The daily entry matching the activity's own start date -- not itself a range.
    sunrise_utc: datetime | None = None
    sunset_utc: datetime | None = None
    # Whether sunset_utc falls inside [start, end] of the activity -- computed at request time
    # from sunset_utc/the activity's own start+duration (never stored as a synthetic
    # activity_metric row, see weather.py's own docstring). None whenever sunset_utc itself is
    # None (nothing to judge against), never a guessed True/False.
    sunset_during_run: bool | None = None
    # The hour-by-hour trajectory across the activity's own window -- the field that replaces a
    # consumer's own second call to Open-Meteo. [] (never omitted, never a fabricated point) when
    # nothing was ever archived for this activity or no hour overlaps the window.
    hourly: list[ActivityWeatherHourlyPointOut] = []


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


class FieldComparisonOut(BaseModel):
    field: str
    self_value: float | str | None
    other_value: float | str | None


class ActivityMergePreviewOut(BaseModel):
    fields: list[FieldComparisonOut]


class ActivityMergeIn(BaseModel):
    other_activity_id: str
    # Only fields chosen "other" need to be sent -- everything else keeps this activity's own
    # current value. See activity_merge.py::MERGEABLE_SCALAR_FIELDS/MERGEABLE_METRIC_FIELDS/
    # MERGEABLE_COLLECTION_FIELDS for the full set of keys this dict may contain.
    field_choices: dict[str, str] = {}


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
