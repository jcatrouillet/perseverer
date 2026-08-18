// Mirrors src/sporthealth/api/schemas/*.py exactly -- field-for-field, same names. Datetimes
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
}

export interface ActivityDetail extends ActivitySummary {
  device: DeviceOut | null;
  laps: LapOut[];
  splits: SplitOut[];
  route: RouteOut | null;
  metrics: ActivityMetricOut[];
  estimated_sweat_loss_ml: number | null;
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
