// Mirrors src/perseverer/api/schemas/*.py exactly -- field-for-field, same names. Datetimes
// arrive as ISO-8601 strings (JSON has no date type); parse with `new Date(...)` at the point
// of use, not here.

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DeviceOut {
  manufacturer: string | null;
  product: string | null;
  serial_number: string | null;
}

export interface LapOut {
  lap_index: number;
  start_time_utc: string;
  duration_s: number | null;
  moving_duration_s: number | null;
  distance_m: number | null;
  avg_hr: number | null;
  max_hr: number | null;
  avg_speed_mps: number | null;
  // m/s -- computed server-side per request from the raw stream (see gap.py::
  // compute_lap_gap_speeds_mps), null for a non-running activity or a lap with no usable stream
  // slice. Convert to min/km the same way ActivityComparisonTable.tsx does for the whole-activity
  // version.
  avg_gap_speed_mps: number | null;
}

export interface SplitOut {
  split_index: number;
  split_type: string | null;
  start_time_utc: string | null;
  end_time_utc: string | null;
  duration_s: number | null;
  distance_m: number | null;
  // Bouldering only, only ever set on a "climb_active" split -- see gap.ts-sibling reasoning in
  // boulderingRoutes.ts for how these two undocumented FIT fields were reverse-engineered.
  climb_grade: number | null;
  climb_result: string | null;
  // A Kaya-sourced route's label (Kaya's own name, else "<hold colour> - <wall>"); null for a
  // Garmin-recorded route.
  climb_name?: string | null;
  // "kaya" for a Kaya-imported route; null for a Garmin/FIT-derived or manual one.
  source?: string | null;
  // Kaya's id for the route (stable across every session it appears in) and the athlete's own note
  // on it (PUT /kaya-climbs/{id}/note) -- the same note on every row of that route, everywhere.
  climb_kaya_id?: string | null;
  note?: string | null;
  // Also bouldering only, but set on both "climb_active" and "climb_rest" splits.
  climb_avg_hr: number | null;
  climb_max_hr: number | null;
  // True only for a route the athlete added by hand -- never set for a FIT-derived row. Only
  // these are ever offered a delete affordance in the UI.
  is_manual: boolean | null;
}

export interface RouteOut {
  encoded_polyline: string | null;
  min_lat: number | null;
  min_lng: number | null;
  max_lat: number | null;
  max_lng: number | null;
  start_lat: number | null;
  start_lng: number | null;
  end_lat: number | null;
  end_lng: number | null;
}

export interface ActivityMetricOut {
  metric_key: string;
  value_num: number | null;
  value_text: string | null;
  unit: string | null;
  source: string;
}

export interface ActivitySummary {
  id: string;
  start_time_utc: string;
  utc_offset_s: number;
  local_date: string | null;
  sport: string;
  sub_sport: string | null;
  name: string | null;
  is_race: boolean | null;
  duration_s: number | null;
  moving_duration_s: number | null;
  distance_m: number | null;
  elevation_gain_m: number | null;
  // Peak altitude reached, not cumulative ascent (elevation_gain_m above).
  max_altitude_m: number | null;
  calories: number | null;
  avg_hr_bpm: number | null;
  max_hr_bpm: number | null;
  training_load: number | null;
  workout_rpe: number | null;
  weight_kg: number | null;
  /** Daniels-Gilbert running performance index (VDOT), GAP-adjusted when a distance/altitude
   * stream is available -- see performance.py. Null for non-running activities or a running
   * activity too short/anomalous for the underlying aerobic model. */
  vdot: number | null;
  /** Garmin Connect's own structured "Workout Builder" name (e.g. "W11 Tue - 4x2km Threshold"),
   * when this activity followed a pre-planned workout -- see yearStats.ts::displayActivityName
   * for why the frontend falls back to this. Null for the (large majority of) activities with
   * no such plan. */
  workout_name: string | null;
  primary_source: string;
  stream_available: boolean;
  /** Bouldering only, all three null for any other activity -- see boulderingRoutes.ts. Exposed
   * here (not just on the detail page's full `splits`) so the activity-card/day-view pill can
   * show them without a second per-activity fetch. */
  climb_route_count: number | null;
  climb_max_completed_grade: number | null;
  climb_time_s: number | null;
}

export interface ActivityDetail extends ActivitySummary {
  device: DeviceOut | null;
  laps: LapOut[];
  splits: SplitOut[];
  route: RouteOut | null;
  metrics: ActivityMetricOut[];
  estimated_sweat_loss_ml: number | null;
  // The athlete's own logged fueling intake -- no vendor source carries this, so both are null
  // until entered via PATCH .../fueling (see sport_override.py's own docstring).
  carbohydrates_g: number | null;
  sodium_mg: number | null;
  // Computed fresh from this activity's own Parquet stream (see transport_mix.py) -- null
  // unless the sport is hiking/walking and a sustained fast segment touches either boundary.
  transport_mix_flag: TransportMixFlagOut | null;
  // True once a trim has been committed for this activity.
  has_trim: boolean;
  // Other activities that look like the same real-world activity as this one (see
  // activity_merge.py::find_duplicate_candidates) -- computed fresh per request, bounded to
  // this activity's own +/-1 day window. Usually empty.
  duplicate_candidates: DuplicateCandidateOut[];
}

export interface TransportMixFlagOut {
  at_start: boolean;
  at_end: boolean;
  suggested_trim_start_s: number | null;
  suggested_trim_end_s: number | null;
}

export interface DuplicateCandidateOut {
  id: string;
  name: string | null;
  primary_source: string;
  start_time_utc: string;
  distance_m: number | null;
  duration_s: number | null;
}

// GET /activities/possible-duplicates -- the Settings page's list-wide duplicate scan, one row
// per relationship (see activity_merge.py::find_all_duplicate_pairs -- reuses the same detection
// as ActivityDetail.duplicate_candidates, deduplicated so each pair appears once).
export interface DuplicatePairOut {
  activity_a: DuplicateCandidateOut;
  activity_b: DuplicateCandidateOut;
}

// GET /activities/needs-trim -- the Settings page's list-wide transport-mix scan.
export interface TrimCandidateOut {
  id: string;
  name: string | null;
  sport: string;
  start_time_utc: string;
  distance_m: number | null;
  duration_s: number | null;
  flag: TransportMixFlagOut;
}

export interface FieldComparisonOut {
  field: string;
  self_value: number | string | null;
  other_value: number | string | null;
}

export interface ActivityMergePreviewOut {
  fields: FieldComparisonOut[];
}

export interface ActivityContextRecentOut {
  id: string;
  local_date: string | null;
  distance_m: number;
  duration_s: number;
  avg_hr_bpm: number | null;
}

export interface ActivityContextOut {
  percentile_rank: number | null;
  comparable_count: number;
  recent: ActivityContextRecentOut[];
  fastest: ActivityContextRecentOut[];
}

export interface ActivityComparisonRowOut {
  id: string;
  local_date: string | null;
  distance_m: number;
  duration_s: number;
  vdot: number | null;
  avg_gap_speed_mps: number | null;
  avg_hr_bpm: number | null;
  avg_cadence_spm: number | null;
}

export interface ActivityComparisonsOut {
  start_radius_m: number;
  distance_band_fraction: number;
  matched_count: number;
  rows: ActivityComparisonRowOut[];
}

export interface ClimbComparisonRowOut {
  id: string;
  local_date: string | null;
  duration_s: number;
  route_count: number;
  max_completed_grade: number | null;
  climb_time_s: number | null;
}

export interface ClimbComparisonsOut {
  duration_band_fraction: number;
  matched_count: number;
  rows: ClimbComparisonRowOut[];
}

export interface ClimbGradeBreakdownOut {
  grade: number;
  attempted: number;
  completed: number;
}

export interface ClimbingSummaryOut {
  session_count: number;
  total_climb_time_s: number;
  total_routes: number;
  max_completed_grade: number | null;
  grade_breakdown: ClimbGradeBreakdownOut[];
}

export interface ActivityMapPointOut {
  id: string;
  local_date: string | null;
  sport: string;
  name: string | null;
  distance_m: number | null;
  start_lat: number;
  start_lng: number;
}

export interface ActivityRouteOut {
  id: string;
  simplified_polyline: string | null;
}

export interface ActivityWeatherHourlyPointOut {
  time_utc: string;
  temperature_c: number | null;
  apparent_temperature_c: number | null;
  dew_point_c: number | null;
  relative_humidity_pct: number | null;
  shortwave_radiation_wm2: number | null;
  cloud_cover_pct: number | null;
  wind_speed_mps: number | null;
  wind_direction_deg: number | null;
  precipitation_mm: number | null;
}

export interface ActivityWeatherOut {
  available: boolean;
  temperature_min_c: number | null;
  temperature_max_c: number | null;
  humidity_min_pct: number | null;
  humidity_max_pct: number | null;
  weather_code: number | null;
  // Single representative (closest-hour) values, independently nullable -- not ranges.
  feels_like_c: number | null;
  wind_speed_mps: number | null;
  wind_direction_deg: number | null;
  // Window-aggregate fields added for heat-stress judgement (dew point, solar radiation, cloud
  // cover, a full apparent-temperature range) -- see weather.py's own module docstring. Null on
  // every activity cached before these existed, until a backfill re-fetches it.
  dew_point_min_c: number | null;
  dew_point_max_c: number | null;
  solar_radiation_max_wm2: number | null;
  solar_radiation_mean_wm2: number | null;
  cloud_cover_min_pct: number | null;
  cloud_cover_max_pct: number | null;
  apparent_temperature_min_c: number | null;
  apparent_temperature_max_c: number | null;
  // A window SUM, not a min/max range -- "how much rain fell during the run." 0.0 is a real
  // reading (no rain); null means no usable precipitation data for this window.
  precipitation_mm: number | null;
  sunrise_utc: string | null;
  sunset_utc: string | null;
  sunset_during_run: boolean | null;
  // The hour-by-hour trajectory across the activity's own window -- replaces a consumer's own
  // second call to Open-Meteo. [] (never omitted) when nothing was ever archived for this
  // activity or no hour overlaps the window.
  hourly: ActivityWeatherHourlyPointOut[];
}

export interface ActivityLocationOut {
  available: boolean;
  location_name: string | null;
}

export interface ActivitySourceOut {
  link_id: number;
  source: string;
  external_id: string;
  ingested_at: string;
  can_split: boolean;
}

export interface ActivityMergeDecisionOut {
  candidate_ref: string;
  reasons: string[];
  decided_at: string;
}

export interface ActivitySourcesOut {
  sources: ActivitySourceOut[];
  merge_decisions: ActivityMergeDecisionOut[];
}

export interface ActivitySplitOut {
  new_activity_id: string;
}

export interface ActivitySportOverrideOut {
  sport: string;
  sub_sport: string | null;
}

export interface ActivityRaceOverrideOut {
  is_race: boolean;
}

export interface ActivityNameOverrideOut {
  name: string;
}

export interface ActivityFuelingOut {
  carbohydrates_g: number | null;
  sodium_mg: number | null;
}

export interface HrZoneConfigOut {
  max_hr_bpm: number | null;
  threshold_hr_bpm: number | null;
  resting_hr_bpm: number | null;
  // Derived from the three fields above (see hr_zones.py::compute_hr_zone_boundaries) -- null
  // unless all three are set.
  zone1_high_bpm: number | null;
  zone2_high_bpm: number | null;
  zone3_high_bpm: number | null;
  zone4_high_bpm: number | null;
}

export interface HrZoneConfigIn {
  max_hr_bpm: number | null;
  threshold_hr_bpm: number | null;
  resting_hr_bpm: number | null;
}

// An athlete's own configured running threshold pace -- the calibration constant
// running_load.py::compute_running_tss needs to turn grade-adjusted pace into a Coggan-style
// rTSS (100 = one hour at threshold pace), used in place of Garmin's own uncalibrated
// training_load_peak for the CTL/ATL/TSB Fitness & Form series. Null means "not configured yet".
export interface RunningLoadConfigOut {
  threshold_pace_sec_per_km: number | null;
}

export interface RunningLoadConfigIn {
  threshold_pace_sec_per_km: number | null;
}

// GET/PUT /settings/profile -- birthdate/height/sex are used only as inputs to formula-based
// fallbacks elsewhere (max HR, BMR) when there isn't enough empirical/device data yet; email is
// the recipient for the opt-in weekly/monthly training-report emails; home_lat/home_lon are the
// one location GET /weather/forecast fetches a forecast for; timezone (a real IANA name, e.g.
// "America/Los_Angeles") is the existing athlete.timezone column, previously CLI-only -- now
// also the timezone GET /weather/forecast requests from Open-Meteo so a forecast day's own date
// lines up with the Week view's local-date grouping. See api/schemas/settings.py::AthleteProfileIn.
export interface AthleteProfileOut {
  birthdate: string | null;
  height_cm: number | null;
  sex: "male" | "female" | null;
  email: string | null;
  home_lat: number | null;
  home_lon: number | null;
  timezone: string;
}

export interface AthleteProfileIn {
  birthdate: string | null;
  height_cm: number | null;
  sex: "male" | "female" | null;
  email: string | null;
  home_lat: number | null;
  home_lon: number | null;
  timezone: string;
}

export type WeekStartDay = "monday" | "sunday";
export type TimeFormat = "24h" | "12h";
export type DefaultView = "week" | "month" | "day" | "activities";
export type UnitPreference = "metric" | "imperial";

// GET/PUT /settings/personalize -- pure display preferences (never read by any backend
// computation, unlike Profile's own fields above). A full-replacement PUT, same contract as
// AthleteProfileIn/Out.
export interface PersonalizeSettingsOut {
  week_start_day: WeekStartDay;
  time_format: TimeFormat;
  default_view: DefaultView;
  unit_preference: UnitPreference;
}

export interface PersonalizeSettingsIn {
  week_start_day: WeekStartDay;
  time_format: TimeFormat;
  default_view: DefaultView;
  unit_preference: UnitPreference;
}

// GET /weather/forecast -- the athlete's own home-location forecast, up to Open-Meteo's own
// 16-day cap. See weather_forecast.py/api/schemas/weather_forecast.py.
export interface ForecastDayOut {
  local_date: string;
  weather_code: number;
  temperature_min_c: number;
  temperature_max_c: number;
}

// One hourly bucket of a single upcoming day -- time_local is naive, in the athlete's own local
// time (NOT UTC, unlike ActivityWeatherHourlyPointOut's own time_utc).
export interface ForecastHourlyPointOut {
  time_local: string;
  temperature_c: number | null;
  apparent_temperature_c: number | null;
  dew_point_c: number | null;
  relative_humidity_pct: number | null;
  shortwave_radiation_wm2: number | null;
  cloud_cover_pct: number | null;
  wind_speed_mps: number | null;
  wind_direction_deg: number | null;
  precipitation_mm: number | null;
}

// The same richer field set ActivityWeatherOut collects for a past run's own window, gathered
// instead for one of the athlete's own upcoming days (UPCOMING_DETAIL_DAYS) -- see
// weather_forecast.py's own module docstring for why feels_like_c/wind_speed_mps/
// wind_direction_deg have no scalar equivalent here and why every datetime field is local.
export interface ForecastDayDetailOut {
  local_date: string;
  weather_code: number | null;
  temperature_min_c: number | null;
  temperature_max_c: number | null;
  humidity_min_pct: number | null;
  humidity_max_pct: number | null;
  dew_point_min_c: number | null;
  dew_point_max_c: number | null;
  solar_radiation_max_wm2: number | null;
  solar_radiation_mean_wm2: number | null;
  cloud_cover_min_pct: number | null;
  cloud_cover_max_pct: number | null;
  apparent_temperature_min_c: number | null;
  apparent_temperature_max_c: number | null;
  precipitation_mm: number | null;
  sunrise_local: string | null;
  sunset_local: string | null;
  hourly: ForecastHourlyPointOut[];
}

export interface WeatherForecastOut {
  available: boolean;
  days: ForecastDayOut[];
  // The near-term rich-conditions detail, from a separate Open-Meteo request than `days` above
  // -- [] whenever that second request fails or returns nothing usable, independent of
  // `available`/`days` above.
  upcoming: ForecastDayDetailOut[];
}

// PUT /settings/password -- self-service password change.
export interface ChangePasswordIn {
  current_password: string;
  new_password: string;
}
export interface ChangePasswordOut {
  success: boolean;
}

// GET/POST /settings/eufy/status,/login -- the web counterpart of `sync athlete
// set-eufy-credentials`.
export interface EufyStatusOut {
  configured: boolean;
  email: string | null;
}
export interface EufyLoginIn {
  email: string;
  password: string;
  device_id: string;
  customer_id: string;
}
export interface EufyLoginOut {
  success: boolean;
}

// GET/POST/DELETE /settings/calendar-feed -- see calendar_feed.py's own module docstring.
export interface CalendarFeedStatusOut {
  enabled: boolean;
  created_at: string | null;
}

export interface CalendarFeedUrlOut {
  url: string;
}

// GET/POST/DELETE /settings/api-key -- see settings.py's own module docstring.
export interface ApiKeyStatusOut {
  enabled: boolean;
  created_at: string | null;
}

export interface ApiKeyOut {
  api_key: string;
}

// GET/PUT /settings/email-reports (+ POST .../test) -- see email_reports.py's module docstring.
export interface EmailReportConfigOut {
  weekly_enabled: boolean;
  monthly_enabled: boolean;
  smtp_configured: boolean;
  recipient_email: string | null;
}

export interface EmailReportConfigIn {
  weekly_enabled: boolean;
  monthly_enabled: boolean;
}

// GET /settings/garmin/status
export interface GarminAuthStatusOut {
  token_store_present: boolean;
  token_store_age_days: number | null;
  last_sync_status: string | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
  // No real Garmin token expiry is readable client-side -- this is the practical substitute
  // (staleness.py's own escalating warning/critical signal). Both null whenever the last sync
  // succeeded.
  staleness_severity: "warning" | "critical" | null;
  staleness_message: string | null;
}

// POST /settings/garmin/login
export interface GarminLoginIn {
  username: string;
  password: string;
}
export interface GarminLoginOut {
  success: boolean;
}

// GET /settings/kaya/status, POST /settings/kaya/login (docs/adr/0016-kaya-bouldering-adapter.md)
export interface KayaStatusOut {
  session_present: boolean;
  session_age_days: number | null;
  last_sync_status: string | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
}
export interface KayaLoginIn {
  email: string;
  password: string;
}
export interface KayaLoginOut {
  success: boolean;
}

// POST /settings/garmin/sync, POST /settings/rebuild, POST /settings/import/bulk-export
export interface JobTriggerOut {
  triggered: boolean;
}

// GET /settings/jobs/latest
export type JobSource = "garmin_connect" | "kaya" | "rebuild" | "garmin_export" | "strava_export";

export interface JobStatusOut {
  source: string;
  status: "running" | "success" | "failed";
  started_at: string;
  finished_at: string | null;
  items_seen: number;
  items_new: number;
  error_count: number;
  first_error: string | null;
}

// POST /activities/{id}/share, POST /periods/{period_type}/share
export interface ShareLinkOut {
  id: number;
  url: string;
}

// POST /share/{id}/revoke
export interface RevokeShareOut {
  revoked: boolean;
}

export interface ActivityWorkoutStepOut {
  step_index: number;
  duration_type: string | null;
  duration_time_s: number | null;
  duration_distance_m: number | null;
  target_type: string | null;
  target_low_mps: number | null;
  target_high_mps: number | null;
  intensity: string | null;
  repeat_from_step: number | null;
  repeat_count: number | null;
}

// --- Scheduled workouts (planned_workout/planned_workout_step) -- see db/schema.py's own
// docstring for the storage shape and workout_syntax.py for the text syntax these steps come
// from. Deliberately a separate type from ActivityWorkoutStepOut above (they're populated from
// different sources -- an authored text parse vs. a device's own recorded FIT workout_mesgs --
// and a planned step can target heart rate or a zone, which a recorded step never could), though
// workoutSteps.ts's expand/group helpers work across both (see that file's own WorkoutStepLike).
export interface PlannedWorkoutStepOut {
  step_index: number;
  duration_type: string | null;
  duration_time_s: number | null;
  duration_distance_m: number | null;
  target_type: string | null; // "pace" | "heart_rate" | null
  target_low: number | null; // m/s for pace, bpm for heart_rate
  target_high: number | null;
  target_hr_zone: number | null;
  cadence_low: number | null;
  cadence_high: number | null;
  intensity: string | null;
  repeat_from_step: number | null;
  repeat_count: number | null;
  // hiit/strength_training only (EXERCISE_SPORTS, planned_workouts.py) -- a specific Garmin
  // exercise picked from garminconnect.exercises' catalog (see data/exerciseCatalog.json), plus
  // a rep count in place of duration_time_s/duration_distance_m (duration_type == "reps").
  duration_reps: number | null;
  exercise_category: string | null;
  exercise_name: string | null;
  weight_kg: number | null;
  // A freeform note on this specific step -- running: parsed from an inline trailing "# comment"
  // token on that step's own source_text line. hiit/strength_training: typed directly against
  // that row in the exercise picker. Never parsed further, never sent to Garmin.
  comment: string | null;
}

/** One step of a hiit/strength_training workout, as authored by the exercise picker -- never
 * parsed from text, unlike running's source_text. See api/schemas/planned_workouts.py's own
 * PlannedWorkoutStepIn. */
export interface PlannedWorkoutStepIn {
  step_index: number;
  // "reps" | "time" for a real step; "repeat_until_steps_cmplt" for a trailing repeat-group
  // marker (repeat_from_step/repeat_count set, every other field omitted) -- same convention
  // ActivityWorkoutStepOut/PlannedWorkoutStepOut already use.
  duration_type: "reps" | "time" | "repeat_until_steps_cmplt";
  duration_time_s?: number | null;
  duration_reps?: number | null;
  intensity?: "active" | "rest" | null;
  repeat_from_step?: number | null;
  repeat_count?: number | null;
  exercise_category?: string | null;
  exercise_name?: string | null;
  weight_kg?: number | null;
  comment?: string | null;
}

export interface ParseErrorOut {
  line_no: number;
  message: string;
}

// One already-repeat-expanded step of a running workout's load estimate -- see
// planned_workout_stats.py's own docstring for the zone/load rules. Rendered as one block in the
// workout's load bar: width proportional to duration_s, color from zone, height from
// intensity_factor (continuous, so two steps sharing one discrete zone still draw at visibly
// different heights -- see WorkoutLoadBar.tsx).
export interface PlannedWorkoutSegmentOut {
  duration_s: number;
  zone: number | null; // 1 (easy) .. 5 (repetition), or null when no zone could be determined
  intensity_factor: number | null; // continuous speed-to-threshold ratio; null iff zone is null
}

// The GPX route attached to a planned running workout (backend: workout_route.py).
export interface PlannedRouteOut {
  name: string | null;
  distance_m: number;
  elevation_gain_m: number | null;
  // Google-encoded polyline (precision 5), thinned to a few hundred points for drawing.
  polyline: string;
  uploaded_at: string;
  // The route pushed to Garmin as a private course alongside the workout push (null until then).
  garmin_course_id?: number | null;
  garmin_course_pushed_at?: string | null;
  // The last failed course push (the workout push itself still succeeded).
  garmin_course_error?: string | null;
}

export interface PlannedWorkoutOut {
  available: boolean;
  id: number | null;
  local_date: string | null;
  sport: string | null;
  name: string | null;
  source_text: string | null;
  scheduled_time: string | null; // "HH:MM", 24h -- display-only, see db/schema.py's own docstring
  // A general note for the whole workout, read before any step -- running/hiit/
  // strength_training only (yoga/bouldering already use source_text as freeform notes).
  // Distinct from a step's own PlannedWorkoutStepOut.comment.
  comment: string | null;
  estimated_duration_s: number | null;
  steps: PlannedWorkoutStepOut[];
  parse_errors: ParseErrorOut[];
  push_status: "draft" | "pushed" | "push_failed" | null;
  push_error: string | null;
  garmin_workout_id: number | null;
  garmin_scheduled_at: string | null;
  // The athlete's own manual "I did this" marker, set/cleared via POST .../complete and
  // .../uncomplete -- independent of push_status (a workout can be completed with no Garmin
  // record of it at all).
  completed_at: string | null;
  // A same-day, matching-sport recorded activity, if one exists -- computed at read time
  // (backend: planned_workouts.py::matching_activity_id), never stored, never overriding
  // completed_at. A companion "this looks done" signal for a workout the athlete never
  // explicitly marked complete, e.g. one already confirmed by a synced Garmin activity.
  matched_activity_id: string | null;
  // running only (planned_workout_stats.py) -- always null/empty for every other sport, and for
  // running itself when the athlete hasn't configured a running-load threshold pace yet
  // (estimated_load only; distance/duration/segments still populate from the steps alone).
  estimated_distance_m: number | null;
  estimated_load: number | null;
  segments: PlannedWorkoutSegmentOut[];
  // The attached GPX route (running only), or null.
  route?: PlannedRouteOut | null;
}

export interface PlannedWorkoutListItemOut {
  local_date: string;
  id: number;
  sport: string;
  name: string | null;
  scheduled_time: string | null;
  push_status: "draft" | "pushed" | "push_failed";
  completed_at: string | null;
  // A same-day, matching-sport recorded activity, if one exists -- computed at read time, never
  // stored, never overriding completed_at. See PlannedWorkoutOut.matched_activity_id.
  matched_activity_id: string | null;
}

export interface RecurringWorkoutOut {
  // Every occurrence date gets its own new row, even one that already had a workout -- a day
  // can hold more than one now, so there's nothing to skip.
  created_dates: string[];
}

// GET/POST/PUT/DELETE /planned-races -- a single upcoming race on the calendar. See
// db/schema.py::planned_race and planned_races.py for the prediction lookup.
export interface PlannedRaceOut {
  id: number;
  local_date: string;
  name: string;
  sport: string;
  distance_m: number;
  scheduled_time: string | null; // "HH:MM", 24h -- optional, display-only
  target_duration_s: number | null; // the athlete's own goal finish time; null = no target
  // Read-only, computed server-side at request time -- never stored.
  days_until: number;
  // The athlete's most recent predicted finish time for this distance (from the Insights
  // performance model) -- only ever set for the four standard race distances
  // (5k/10k/half/marathon); null for a custom distance or before any performance rollup exists.
  predicted_duration_s: number | null;
}

export interface PlannedRaceIn {
  local_date: string;
  name: string;
  sport: string;
  distance_m: number;
  scheduled_time: string | null;
  target_duration_s: number | null;
}

// GET/POST/PUT/DELETE /blood-tests -- athlete-entered blood test results, one row per marker per
// draw. Several rows sharing one local_date form one logical panel. Reference ranges are the
// athlete's own, from their lab report -- informational only, never a claim this app makes.
export interface BloodTestResultOut {
  id: number;
  local_date: string;
  marker: string;
  value_num: number;
  unit: string | null;
  reference_low: number | null;
  reference_high: number | null;
  lab_name: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface BloodTestResultIn {
  local_date: string;
  marker: string;
  value_num: number;
  unit?: string | null;
  reference_low?: number | null;
  reference_high?: number | null;
  lab_name?: string | null;
  notes?: string | null;
}

/** One marker within a `BloodTestBatchIn` -- the shared local_date/lab_name/notes live on the
 * batch itself, not repeated per marker. */
export interface BloodTestMarkerIn {
  marker: string;
  value_num: number;
  unit?: string | null;
  reference_low?: number | null;
  reference_high?: number | null;
}

export interface BloodTestBatchIn {
  local_date: string;
  lab_name?: string | null;
  notes?: string | null;
  results: BloodTestMarkerIn[];
}

export interface ActivityWorkoutOut {
  name: string | null;
  description: string | null;
  steps: ActivityWorkoutStepOut[];
}

export interface InsightOut {
  kind: string;
  window: string;
  title: string;
  detail: Record<string, unknown>;
  value_num: number | null;
  metric_key: string | null;
  sport_family: string | null;
  activity_id: string | null;
  local_date: string | null;
  computed_at: string;
}

/** One `pace_bands.PACE_BANDS` entry, total seconds across the athlete's whole running history --
 * always present for every defined band, even when `seconds` is 0. */
export interface PaceBandOut {
  label: string;
  seconds: number;
}

/** One running activity's own time-in-band breakdown -- `bands` always lists every band, in the
 * same fixed order, even ones this activity spent 0 seconds in. */
export interface ActivityPaceBandsOut {
  activity_id: string;
  local_date: string | null;
  bands: PaceBandOut[];
}

export interface StreamResponse {
  activity_id: string;
  tier: string;
  channels: string[];
  timestamps: string[];
  series: Record<string, (number | null)[]>;
}

export interface HealthObservationOut {
  metric_key: string;
  observed_at_utc: string;
  local_date: string;
  aggregation: string;
  value_num: number | null;
  value_text: string | null;
  unit: string | null;
  source: string;
}

export interface SleepStageOut {
  stage: string;
  start_time_utc: string;
  end_time_utc: string;
}

export interface SleepSessionOut {
  local_date: string;
  start_time_utc: string;
  end_time_utc: string;
  total_sleep_s: number | null;
  sleep_score: number | null;
  source: string;
  stages: SleepStageOut[];
}

export interface HealthMetricRollupOut {
  metric_key: string;
  value_sum: number | null;
  value_avg: number | null;
  value_min: number | null;
  value_max: number | null;
  value_last: number | null;
  n_observations: number;
}

export interface DayRollupOut {
  local_date: string;
  activity_count: number;
  activity_duration_s: number | null;
  activity_moving_duration_s: number | null;
  activity_distance_m: number | null;
  activity_elevation_gain_m: number | null;
  activity_calories: number | null;
  sleep_total_s: number | null;
  sleep_score: number | null;
  health_metrics: HealthMetricRollupOut[];
}

export interface CalendarResponse {
  days: DayRollupOut[];
}

// --- Period (week/month) rollups, Fitness & Form, health dashboard (Phase 6) -- see
// docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.

export interface PeriodHealthMetricRollupOut {
  metric_key: string;
  value_sum: number | null;
  value_avg: number | null;
  value_min: number | null;
  value_max: number | null;
  value_last: number | null;
  n_observations: number;
}

export type PeriodType = "week" | "month";

export interface PeriodRollupOut {
  period_type: PeriodType;
  period_start: string;
  period_end: string;
  activity_count: number;
  activity_duration_s: number | null;
  activity_moving_duration_s: number | null;
  activity_distance_m: number | null;
  activity_elevation_gain_m: number | null;
  activity_calories: number | null;
  activity_days_count: number;
  sleep_total_s: number | null;
  sleep_score: number | null;
  health_metrics: PeriodHealthMetricRollupOut[];
}

export interface PeriodCalendarResponse {
  periods: PeriodRollupOut[];
}

export interface FitnessDailyRollupOut {
  local_date: string;
  training_load: number;
  ctl: number;
  atl: number;
  tsb: number;
}

export interface PerformanceDailyRollupOut {
  local_date: string;
  rolling_vdot: number | null;
  max_hr_bpm: number | null;
  // "empirical" (real observed max HR) | "formula_fallback" (Tanaka, only when there's no
  // empirical value yet and the athlete has a configured birthdate) | null.
  max_hr_source: "empirical" | "formula_fallback" | null;
  threshold_pace_s_per_km: number | null;
  threshold_hr_bpm: number | null;
  threshold_hr_source: "empirical" | "fallback" | null;
  aerobic_threshold_pace_s_per_km: number | null;
  aerobic_threshold_hr_bpm: number | null;
  aerobic_threshold_hr_source: "empirical" | "fallback" | null;
  predicted_5k_s: number | null;
  predicted_10k_s: number | null;
  predicted_half_marathon_s: number | null;
  predicted_marathon_s: number | null;
}

export interface Vo2maxContributorOut {
  activity_id: string;
  local_date: string;
  name: string | null;
  sport: string;
  distance_m: number | null;
  duration_s: number | null;
  vdot: number;
}

export interface Vo2maxFactorAnalysisOut {
  as_of: string;
  window_start: string;
  window_end: string;
  rolling_vdot: number | null;
  driving_activity: Vo2maxContributorOut | null;
  other_contributors: Vo2maxContributorOut[];
  expires_on: string | null;
  days_since_last_qualifying_run: number | null;
  missing: string[];
}

export interface ActivityRefOut {
  activity_id: string;
  local_date: string;
  name: string | null;
  sport: string;
  distance_m: number | null;
  duration_s: number | null;
}

export interface ZoneRunSampleOut extends ActivityRefOut {
  pace_s_per_km: number;
  avg_hr_bpm: number;
}

export interface PaceHrZoneOut {
  number: number;
  label: string;
  description: string;
  // Faster/slower edges of the pace band (seconds/km) -- "fast" is always <= "slow" numerically.
  // Either can be null at the two open ends (zone 1 has no slow-side floor, zone 5 no fast-side
  // ceiling).
  pace_fast_s_per_km: number | null;
  pace_slow_s_per_km: number | null;
  hr_low_bpm: number | null;
  hr_high_bpm: number | null;
  hr_source: "empirical" | "formula_fallback" | null;
  qualifying_run_count: number;
  // Every qualifying run's own avg HR (evenly sampled down to a cap when there are many),
  // sorted ascending by avg_hr_bpm -- the "why these numbers" evidence, same instinct
  // Vo2maxFactorAnalysisOut's own contributor lists already establish for a single point.
  sample_runs: ZoneRunSampleOut[];
}

export interface PaceHrZonesOut {
  as_of: string;
  // The single best (highest) VDOT/max HR within its own trailing lookback window (races/max HR
  // get 2 years, a training-run fallback gets 1 year -- see pace_hr_zones.py's "How far back"
  // docstring section), not the athlete's literal entire history and not a short rolling
  // "current fitness" value either. profile_vdot_activity is the one run that set it; max HR has
  // no such activity when it's the Tanaka-formula fallback.
  profile_vdot: number | null;
  profile_vdot_activity: ActivityRefOut | null;
  // "race" when profile_vdot came from an activity marked as a race within the last 2 years
  // (preferred -- VDOT is calibrated against genuine race efforts); "training_run" when no race
  // qualifies and this fell back to the best training run in the last year instead (see the
  // accompanying `missing` caveat).
  profile_vdot_source: "race" | "training_run" | null;
  profile_max_hr_bpm: number | null;
  profile_max_hr_source: "empirical" | "formula_fallback" | null;
  zones: PaceHrZoneOut[];
  missing: string[];
}

// GET /performance/race-readiness -- see race_readiness.py's own module docstring for the full
// model (recency-weighted weekly-distance/long-run compliance against the athlete's next
// upcoming running race, combined into one readiness percentage; predicted_duration_s reuses the
// existing VDOT-based prediction, shown alongside, never blended into readiness_pct).
export interface RaceReadinessPointOut {
  as_of: string;
  weekly_distance_compliance_pct: number;
  long_run_compliance_pct: number;
  readiness_pct: number;
}

// One entry per Monday-start week (0.0 never omitted) -- the actual realized numbers a
// dedicated chart plots real bars for against a target reference line, distinct from
// RaceReadinessPointOut's own already-weighted/combined percentages.
export interface RaceReadinessWeekOut {
  week_start: string;
  distance_m: number;
}

export interface RaceReadinessOut {
  available: boolean;
  race_id: number | null;
  race_name: string | null;
  race_local_date: string | null;
  race_distance_m: number | null;
  weekly_distance_target_m: number | null;
  long_run_target_m: number | null;
  as_of: string | null;
  current: RaceReadinessPointOut | null;
  predicted_duration_s: number | null;
  history: RaceReadinessPointOut[];
  weekly_distance_series: RaceReadinessWeekOut[];
  long_run_series: RaceReadinessWeekOut[];
}

// GET /performance/curve -- the best sustained value for each of a fixed set of durations,
// across every qualifying activity in range. See performance_curve.py's own module docstring.
export interface PerformanceCurvePointOut {
  duration_s: number;
  value: number;
  // The activity that actually set this bucket's record.
  activity_id: string;
  local_date: string;
}

export interface PerformanceCurveOut {
  available: boolean;
  metric: "pace" | "gap" | "heart_rate";
  points: PerformanceCurvePointOut[];
  // The athlete's own already-computed threshold pace/HR, shown alongside the curve as
  // reference lines -- never blended into it. Only the pair relevant to `metric` is ever
  // non-null.
  threshold_pace_s_per_km: number | null;
  aerobic_threshold_pace_s_per_km: number | null;
  threshold_hr_bpm: number | null;
  aerobic_threshold_hr_bpm: number | null;
  max_hr_bpm: number | null;
}

export interface HealthDashboardDayOut {
  local_date: string;
  value_sum: number | null;
  value_avg: number | null;
  value_min: number | null;
  value_max: number | null;
  value_last: number | null;
  n_observations: number;
  source_metric_key: string;
}

export interface HealthDashboardMetricOut {
  logical_metric: string;
  last_observed: string | null;
  daily: HealthDashboardDayOut[];
}

export interface HealthDashboardOut {
  metrics: HealthDashboardMetricOut[];
}

// GET /health/stream -- the intraday health_stream/Parquet series (currently only
// garmin.daily_body_battery.level), distinct from HealthObservationOut's once-or-a-few-per-day
// EAV rows.
export interface HealthStreamResponse {
  metric_key: string;
  local_date: string;
  timestamps: string[];
  values: number[];
}

export type EntityType = "activity" | "day" | "week";

export interface NoteOut {
  id: number;
  entity_type: EntityType;
  entity_id: string;
  body: string;
  author: string | null;
  created_at: string;
  updated_at: string;
}

export interface NoteCreate {
  entity_type: EntityType;
  entity_id: string;
  body: string;
  author?: string | null;
}

export interface NoteUpdate {
  body: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  expires_at: string;
}

export interface GoalOut {
  id: number;
  period_type: "week" | "month" | "year";
  period_start: string;
  sport: string | null;
  target_distance_m: number;
}

export interface GoalProgressPoint {
  local_date: string;
  cumulative_distance_m: number;
}

export interface GoalProgressOut {
  available: boolean;
  goal: GoalOut | null;
  period_end: string | null;
  daily: GoalProgressPoint[];
  target_per_day_m: number | null;
  current_distance_m: number | null;
  target_distance_as_of_today_m: number | null;
  ahead_behind_m: number | null;
  pct_complete: number | null;
}

/** A duration goal: a target amount of TIME (seconds) on one sport, or on every sport combined
 * (`sport: null`), in a week/month/year. `period_start` as for the other goal kinds. */
export interface DurationGoalOut {
  id: number;
  period_type: "week" | "month" | "year";
  period_start: string;
  sport: string | null;
  target_duration_s: number;
}

export interface DurationGoalIn {
  period_type: "week" | "month" | "year";
  period_start: string;
  sport: string | null;
  target_duration_s: number;
}

export interface DurationGoalProgressPoint {
  local_date: string;
  cumulative_duration_s: number;
}

export interface DurationGoalProgressOut {
  goal: DurationGoalOut;
  period_end: string;
  daily: DurationGoalProgressPoint[];
  target_per_day_s: number;
  current_duration_s: number;
  target_as_of_today_s: number;
  ahead_behind_s: number;
  pct_complete: number;
}

export interface DurationGoalRepeatOut {
  created: DurationGoalOut[];
  skipped_period_starts: string[];
}

/** Response of POST /goals/repeat: the same weekly goal on every week it covers. */
export interface GoalRepeatOut {
  goals: GoalOut[];
}

/** Response of POST /bouldering-goals/repeat. Weeks that already held an identical goal are left
 * as they were and listed in `skipped_period_starts`. */
export interface BoulderingGoalRepeatOut {
  created: BoulderingGoalOut[];
  skipped_period_starts: string[];
}

/** A bouldering goal: a target number of COMPLETED routes in a week/month/year, at one V-grade
 * (optionally "or harder") or any grade (`grade: null`). `period_start` is "YYYY" for a year,
 * "YYYY-MM" for a month, or the ISO date a 7-day week starts on. */
export interface BoulderingGoalOut {
  id: number;
  period_type: "week" | "month" | "year";
  period_start: string;
  grade: number | null;
  and_harder: boolean;
  target_count: number;
}

export interface BoulderingGoalIn {
  period_type: "week" | "month" | "year";
  period_start: string;
  grade: number | null;
  and_harder: boolean;
  target_count: number;
}

export interface BoulderingGoalProgressPoint {
  local_date: string;
  cumulative_count: number;
}

export interface BoulderingGoalProgressOut {
  goal: BoulderingGoalOut;
  period_end: string;
  daily: BoulderingGoalProgressPoint[];
  target_per_day: number;
  current_count: number;
  target_as_of_today: number;
  ahead_behind: number;
  pct_complete: number;
}

export interface ShoeIn {
  brand: string;
  model: string;
  size?: string | null;
  comments?: string | null;
  initial_distance_km: number;
  max_distance_km: number | null;
}

export interface ShoeOut extends ShoeIn {
  id: string;
  distance_km: number;
  remaining_km: number | null;
  over_limit: boolean;
  retired: boolean;
  default_sports: string[];
  created_at: string;
}

export interface GearAlertOut {
  shoe_id: string;
  brand: string;
  model: string;
  distance_km: number;
  max_distance_km: number;
}

export interface ActivityShoeOut {
  shoe_id: string | null;
  // True when shoe_id came from the athlete's dated sport default, not an explicit per-activity
  // choice -- see gear.py::resolve_activity_shoe.
  is_default: boolean;
}
