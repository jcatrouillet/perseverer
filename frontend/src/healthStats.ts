import type { HealthDashboardMetricOut, HealthObservationOut, SleepSessionOut } from "./api/types";
import { isoDate, mondayOf, parseIsoDate } from "./dateUtils";

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
  /** Epoch ms (UTC midnight of local_date) -- lets the chart plot on a true time scale rather
   * than evenly-spaced categories, so a week-long gap between two Eufy readings actually reads
   * as a gap instead of looking identical to two consecutive days. */
  ts: number;
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
      const point =
        byDate.get(day.local_date) ??
        ({
          local_date: day.local_date,
          ts: new Date(`${day.local_date}T00:00:00Z`).getTime(),
        } as MergedTrendPoint);
      point[key] = value;
      byDate.set(day.local_date, point);
    }
  }
  return Array.from(byDate.values()).sort((a, b) => a.ts - b.ts);
}

export interface WeeklySleepPoint {
  weekStart: string;
  avgHours: number | null;
}

/** One bucket per Monday-starting ISO week from `startDate` through `endDate`, each holding the
 * *average* nightly sleep duration among that week's sessions -- same "pad every week, null (not
 * zero) for one with nothing" convention as runningStats.ts's weeklyDistanceSeries/
 * weeklyBestVdotSeries, just averaged instead of summed since a missing night shouldn't drag a
 * week's average toward zero the way it would drag a distance total down. */
export function weeklyAverageSleepHours(
  sessions: SleepSessionOut[],
  startDate: string,
  endDate: string,
): WeeklySleepPoint[] {
  const hoursByWeek = new Map<string, number[]>();
  for (const s of sessions) {
    if (s.total_sleep_s == null) continue;
    const monday = isoDate(mondayOf(parseIsoDate(s.local_date)));
    const hours = hoursByWeek.get(monday) ?? [];
    hours.push(s.total_sleep_s / 3600);
    hoursByWeek.set(monday, hours);
  }

  const points: WeeklySleepPoint[] = [];
  const cursor = mondayOf(parseIsoDate(startDate));
  const last = parseIsoDate(endDate);
  while (cursor <= last) {
    const weekStart = isoDate(cursor);
    const hours = hoursByWeek.get(weekStart);
    points.push({
      weekStart,
      avgHours:
        hours && hours.length > 0
          ? Math.round((hours.reduce((a, b) => a + b, 0) / hours.length) * 10) / 10
          : null,
    });
    cursor.setUTCDate(cursor.getUTCDate() + 7);
  }
  return points;
}

export interface MonthlySleepPoint {
  /** "YYYY-MM" */
  month: string;
  avgHours: number | null;
}

/** One bucket per calendar month from `startDate` through `endDate` (both "YYYY-MM-DD"), each
 * holding the average nightly sleep duration among that month's sessions -- the monthly
 * counterpart to weeklyAverageSleepHours above, for the all-time view's much longer span. */
export function monthlyAverageSleepHours(
  sessions: SleepSessionOut[],
  startDate: string,
  endDate: string,
): MonthlySleepPoint[] {
  const hoursByMonth = new Map<string, number[]>();
  for (const s of sessions) {
    if (s.total_sleep_s == null) continue;
    const month = s.local_date.slice(0, 7);
    const hours = hoursByMonth.get(month) ?? [];
    hours.push(s.total_sleep_s / 3600);
    hoursByMonth.set(month, hours);
  }

  const points: MonthlySleepPoint[] = [];
  const cursor = new Date(
    Date.UTC(Number(startDate.slice(0, 4)), Number(startDate.slice(5, 7)) - 1, 1),
  );
  const last = new Date(Date.UTC(Number(endDate.slice(0, 4)), Number(endDate.slice(5, 7)) - 1, 1));
  while (cursor <= last) {
    const month = `${cursor.getUTCFullYear()}-${String(cursor.getUTCMonth() + 1).padStart(2, "0")}`;
    const hours = hoursByMonth.get(month);
    points.push({
      month,
      avgHours:
        hours && hours.length > 0
          ? Math.round((hours.reduce((a, b) => a + b, 0) / hours.length) * 10) / 10
          : null,
    });
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  return points;
}
