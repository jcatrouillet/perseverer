import type { HealthDashboardMetricOut } from "./api/types";

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
