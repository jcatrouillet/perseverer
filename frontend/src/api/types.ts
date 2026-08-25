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

// POST /settings/garmin/sync, POST /settings/rebuild, POST /settings/import/bulk-export
export interface JobTriggerOut {
  triggered: boolean;
}

// GET /settings/jobs/latest
export type JobSource = "garmin_connect" | "rebuild" | "garmin_export" | "strava_export";

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

export type EntityType = "activity" | "day";

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
  period_type: "year" | "month";
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
