// Data for the period summaries' Cycling section (CyclingStats.tsx). Built on the same helpers as
// the Running section (runningStats.ts), over rides only.
import type { ActivitySummary } from "./api/types";
import { isoDate, mondayOf, parseIsoDate } from "./dateUtils";
import { effectiveDurationS, rollingDistanceKm } from "./runningStats";

export interface TrailingPoint {
  local_date: string;
  /** km over the `windowDays` days ending that date. */
  km: number;
}

/** One point per day of [startDate, endDate] with the trailing `windowDays`-day ride distance (7
 * for a month, 30 for a year, 365 for all time). The window reaches back before `startDate`, so
 * pass every ride (`history`), not only the period's: a 30-day total on 1 January must count
 * December's rides. */
export function trailingDistance(
  history: ActivitySummary[],
  startDate: string,
  endDate: string,
  windowDays: number,
): TrailingPoint[] {
  // rollingDistanceKm sums from the first day of its own series, so the series starts a whole
  // window early and the lead-in days are dropped: day one of the period then already counts
  // the previous days' rides.
  const lead = parseIsoDate(startDate);
  lead.setUTCDate(lead.getUTCDate() - (windowDays - 1));
  return rollingDistanceKm(history, isoDate(lead), endDate, windowDays)
    .filter((p) => p.local_date >= startDate)
    .map((p) => ({ local_date: p.local_date, km: Math.round(p.distanceM / 100) / 10 }));
}

export interface PowerPoint {
  id: string;
  km: number;
  watts: number;
}

/** Rides with a power meter, as (distance, average power) points. */
export function powerVsDistance(rides: ActivitySummary[]): PowerPoint[] {
  return rides
    .filter((a) => a.avg_power_w != null && a.avg_power_w > 0 && (a.distance_m ?? 0) > 0)
    .map((a) => ({
      id: a.id,
      km: Math.round((a.distance_m! / 1000) * 10) / 10,
      watts: Math.round(a.avg_power_w!),
    }));
}

/** Average moving speed over the period's rides (total distance / total moving time), km/h. */
export function averageSpeedKmh(rides: ActivitySummary[]): number | null {
  let distanceM = 0;
  let seconds = 0;
  for (const a of rides) {
    const s = effectiveDurationS(a);
    if (a.distance_m == null || s == null || s <= 0) continue;
    distanceM += a.distance_m;
    seconds += s;
  }
  return seconds > 0 ? distanceM / 1000 / (seconds / 3600) : null;
}

export interface WeeklyBestFtpPoint {
  weekStart: string;
  /** FTP-equivalent watts: 95% of the week's best 20-minute power; null with no power ride. */
  ftp: number | null;
  activityId: string | null;
}

/** One point per Monday-start week of [startDate, endDate]: 95% of the best 20-minute power of
 * that week's rides, the same FTP rule as the Insights tab (ride_power.py), but per week. */
export function weeklyBestFtp(
  rides: ActivitySummary[],
  startDate: string,
  endDate: string,
): WeeklyBestFtpPoint[] {
  const best = new Map<string, { watts: number; id: string }>();
  for (const a of rides) {
    if (!a.local_date || !a.best_20min_power_w || a.best_20min_power_w <= 0) continue;
    const week = isoDate(mondayOf(parseIsoDate(a.local_date)));
    if (a.best_20min_power_w > (best.get(week)?.watts ?? 0)) {
      best.set(week, { watts: a.best_20min_power_w, id: a.id });
    }
  }
  const points: WeeklyBestFtpPoint[] = [];
  const cursor = mondayOf(parseIsoDate(startDate));
  const last = parseIsoDate(endDate);
  while (cursor <= last) {
    const iso = isoDate(cursor);
    const b = best.get(iso);
    points.push({
      weekStart: iso,
      ftp: b ? Math.round(b.watts * 0.95) : null,
      activityId: b?.id ?? null,
    });
    cursor.setUTCDate(cursor.getUTCDate() + 7);
  }
  return points;
}
