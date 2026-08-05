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
  local_date: string | null;
  sport: string;
  sub_sport: string | null;
  name: string | null;
  duration_s: number | null;
  distance_m: number | null;
  elevation_gain_m: number | null;
  calories: number | null;
  primary_source: string;
  stream_available: boolean;
}

export interface ActivityDetail extends ActivitySummary {
  moving_duration_s: number | null;
  device: DeviceOut | null;
  laps: LapOut[];
  splits: SplitOut[];
  route: RouteOut | null;
  metrics: ActivityMetricOut[];
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
