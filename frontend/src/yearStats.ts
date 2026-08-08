// Pure computations over the FULL activity list (any sport) for the top-level "<year> stats"
// section -- a general-purpose counterpart to runningStats.ts, which is sport-scoped.
import type { ActivitySummary } from "./api/types";
import { isoDate, mondayOf, parseIsoDate } from "./dateUtils";
import { effectiveDurationS } from "./runningStats";

export function totalElevationM(activities: ActivitySummary[]): number {
  return activities.reduce((sum, a) => sum + (a.elevation_gain_m ?? 0), 0);
}

export interface LongestActivity {
  distanceM: number;
  date: string;
  sport: string;
}

export function longestActivity(activities: ActivitySummary[]): LongestActivity | null {
  const withDistance = activities.filter((a) => a.distance_m != null);
  if (withDistance.length === 0) return null;
  const longest = withDistance.reduce((max, a) => (a.distance_m! > max.distance_m! ? a : max));
  return {
    distanceM: longest.distance_m!,
    date: longest.local_date ?? longest.start_time_utc.slice(0, 10),
    sport: longest.sport,
  };
}

/** The calendar month (1-12) with the most activities. Ties resolve to the earliest month. */
export function busiestMonth(activities: ActivitySummary[]): number | null {
  const counts = new Array(12).fill(0) as number[];
  for (const a of activities) {
    if (!a.local_date) continue;
    counts[Number(a.local_date.slice(5, 7)) - 1] += 1;
  }
  const max = Math.max(...counts);
  if (max === 0) return null;
  return counts.indexOf(max) + 1;
}

/** The calendar year with the most activities -- the all-time-view analog of busiestMonth.
 * Ties resolve to the earliest year. */
export function busiestYear(activities: ActivitySummary[]): number | null {
  const counts = new Map<number, number>();
  for (const a of activities) {
    if (!a.local_date) continue;
    const year = Number(a.local_date.slice(0, 4));
    counts.set(year, (counts.get(year) ?? 0) + 1);
  }
  let best: number | null = null;
  let bestCount = 0;
  for (const year of Array.from(counts.keys()).sort((a, b) => a - b)) {
    const count = counts.get(year)!;
    if (count > bestCount) {
      bestCount = count;
      best = year;
    }
  }
  return best;
}

/** The Monday-starting week (by ISO date) with the most activities -- the month-view analog of
 * busiestMonth. Ties resolve to the earliest week. */
export function busiestWeekStart(activities: ActivitySummary[]): string | null {
  const counts = new Map<string, number>();
  for (const a of activities) {
    if (!a.local_date) continue;
    const monday = isoDate(mondayOf(parseIsoDate(a.local_date)));
    counts.set(monday, (counts.get(monday) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestCount = 0;
  for (const monday of Array.from(counts.keys()).sort()) {
    const count = counts.get(monday)!;
    if (count > bestCount) {
      bestCount = count;
      best = monday;
    }
  }
  return best;
}

export function averageDistanceM(activities: ActivitySummary[]): number | null {
  const withDistance = activities.filter((a) => a.distance_m != null);
  if (withDistance.length === 0) return null;
  return withDistance.reduce((sum, a) => sum + a.distance_m!, 0) / withDistance.length;
}

/** Prefers moving time over elapsed time per activity, same reasoning as
 * runningStats.ts::effectiveDurationS. */
export function averageDurationS(activities: ActivitySummary[]): number | null {
  const withDuration = activities.filter((a) => effectiveDurationS(a) != null);
  if (withDuration.length === 0) return null;
  return withDuration.reduce((sum, a) => sum + effectiveDurationS(a)!, 0) / withDuration.length;
}

/** Weighted (sum distance / sum time), not a naive average of each activity's own speed --
 * the same "weighted, not average-of-averages" rule used throughout this project's own period
 * rollups (see docs/adr/0009-phase-6-calendar-rollups-fitness-health.md decision 2). */
export function averageSpeedKmh(activities: ActivitySummary[]): number | null {
  const eligible = activities.filter((a) => a.distance_m != null && effectiveDurationS(a) != null);
  const totalDistanceM = eligible.reduce((sum, a) => sum + a.distance_m!, 0);
  const totalDurationS = eligible.reduce((sum, a) => sum + effectiveDurationS(a)!, 0);
  if (totalDurationS === 0) return null;
  return totalDistanceM / 1000 / (totalDurationS / 3600);
}

/** A simple mean across activities, unlike distance/speed -- heart rate has no natural
 * "sum" semantics to weight by. */
export function averageHeartRateBpm(activities: ActivitySummary[]): number | null {
  const withHr = activities.filter((a) => a.avg_hr_bpm != null);
  if (withHr.length === 0) return null;
  return withHr.reduce((sum, a) => sum + a.avg_hr_bpm!, 0) / withHr.length;
}

export function maxHeartRateBpm(activities: ActivitySummary[]): number | null {
  const values = activities.map((a) => a.max_hr_bpm).filter((v): v is number => v != null);
  return values.length > 0 ? Math.max(...values) : null;
}

export interface ActivityTypeCount {
  sport: string;
  count: number;
}

// FIT's "training" sport is a generic container for indoor cardio/strength/mindfulness work --
// Garmin Connect itself never shows "Training" as a category, only the specific sub_sport
// (Yoga, Strength Training, Breathwork). Every other sport in this dataset has a real name of
// its own (sub_sport is just 'generic'), so this substitution is scoped to "training" only.
const GENERIC_CONTAINER_SPORTS = new Set(["training"]);

function displaySport(activity: ActivitySummary): string {
  if (GENERIC_CONTAINER_SPORTS.has(activity.sport) && activity.sub_sport) {
    return activity.sub_sport;
  }
  return activity.sport;
}

/** Descending by count, ties broken alphabetically for a stable, deterministic order. */
export function activityTypeCounts(activities: ActivitySummary[]): ActivityTypeCount[] {
  const counts = new Map<string, number>();
  for (const a of activities) {
    const sport = displaySport(a);
    counts.set(sport, (counts.get(sport) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([sport, count]) => ({ sport, count }))
    .sort((a, b) => b.count - a.count || a.sport.localeCompare(b.sport));
}
