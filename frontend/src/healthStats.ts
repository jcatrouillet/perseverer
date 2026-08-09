import type { HealthDashboardMetricOut, HealthObservationOut } from "./api/types";

/** Weighted average across every day with data -- `sum(value_sum) / sum(n_observations)`, the
 * same "weighted, not naive average-of-averages" rule the backend's own period rollups use
 * (see docs/adr/0009-phase-6-calendar-rollups-fitness-health.md decision 2), applied here at
 * the client since there's no year-grain rollup endpoint to read it from directly. */
export function weightedAverage(metric: HealthDashboardMetricOut | undefined): number | null {
  if (!metric) return null;
  let sumValues = 0;
  let sumObservations = 0;
  for (const day of metric.daily) {
    if (day.value_sum != null && day.n_observations > 0) {
      sumValues += day.value_sum;
      sumObservations += day.n_observations;
    }
  }
  return sumObservations > 0 ? sumValues / sumObservations : null;
}

/** A metric's value for one specific day -- `value_avg` falling back to `value_last`, matching
 * `mergeTrendSeries`'s own choice below so a single-day readout (a wellness strip) and a trend
 * chart never disagree about which field represents "the" value for a day that has more than
 * one observation. */
export function valueForDate(
  metric: HealthDashboardMetricOut | undefined,
  localDate: string,
): number | null {
  if (!metric) return null;
  const day = metric.daily.find((d) => d.local_date === localDate);
  if (!day) return null;
  return day.value_avg ?? day.value_last;
}

/** The freshest raw observation for one metric_key -- some `health_observation` metrics (device-
 * pushed readings like Training Readiness, not the daily-aggregated `health_metric_daily_rollup`
 * ones `valueForDate` reads) arrive several times a day, and "today's" reading is conventionally
 * the latest one, matching how Garmin Connect itself shows a single current value rather than
 * a list. Returns null when the metric has no observation at all (rather than picking an
 * arbitrary one), so a caller can tell "no data" from "data, but somehow unordered". */
export function latestObservation(
  observations: HealthObservationOut[],
  metricKey: string,
): HealthObservationOut | null {
  const matches = observations.filter((o) => o.metric_key === metricKey);
  if (matches.length === 0) return null;
  return matches.reduce((latest, o) => (o.observed_at_utc > latest.observed_at_utc ? o : latest));
}

export interface MergedTrendPoint {
  local_date: string;
  [logicalMetric: string]: string | number | null;
}

/** One row per distinct local_date across the given metrics, each metric's day's `value_avg`
 * (falling back to `value_last`) under its own logical_metric key -- the shape Recharts needs
 * for one chart with several lines from otherwise-independent per-metric daily arrays. */
export function mergeTrendSeries(
  metrics: HealthDashboardMetricOut[],
  keys: string[],
): MergedTrendPoint[] {
  const byDate = new Map<string, MergedTrendPoint>();
  for (const key of keys) {
    const metric = metrics.find((m) => m.logical_metric === key);
    if (!metric) continue;
    for (const day of metric.daily) {
      const value = day.value_avg ?? day.value_last;
      if (value == null) continue;
      const point = byDate.get(day.local_date) ?? { local_date: day.local_date };
      point[key] = value;
      byDate.set(day.local_date, point);
    }
  }
  return Array.from(byDate.values()).sort((a, b) => a.local_date.localeCompare(b.local_date));
}
