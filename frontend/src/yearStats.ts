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

export interface ActivityDayGroup {
  localDate: string;
  activities: ActivitySummary[];
}

/** Groups a flat activity list into one bucket per local_date, preserving each group's
 * position at its first-encountered activity's index -- so grouping a `GET /activities` page
 * (already sorted newest-first) yields days newest-first too, with no separate sort step here
 * that could disagree with the caller's own ordering. Falls back to the UTC calendar date only
 * for the (should-be-nonexistent, post-Phase-6-fix) case of a null local_date. */
export function groupByLocalDate(activities: ActivitySummary[]): ActivityDayGroup[] {
  const groups = new Map<string, ActivitySummary[]>();
  for (const a of activities) {
    const key = a.local_date ?? a.start_time_utc.slice(0, 10);
    const existing = groups.get(key);
    if (existing) existing.push(a);
    else groups.set(key, [a]);
  }
  return Array.from(groups.entries()).map(([localDate, dayActivities]) => ({
    localDate,
    activities: dayActivities,
  }));
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
  /** Summed effective duration (moving-preferred, see effectiveDurationS) across every activity
   * of this sport -- the "time" half of the count-vs-time toggle on the activities-by-type
   * chart. Activities with no duration at all don't contribute, same as everywhere else this
   * project sums durations. */
  durationS: number;
}

// FIT's "training" sport is a generic container for indoor cardio/strength/mindfulness work --
// Garmin Connect itself never shows "Training" as a category, only the specific sub_sport
// (Yoga, Strength Training, Breathwork). Every other sport in this dataset has a real name of
// its own (sub_sport is just 'generic'), so this substitution is scoped to "training" only.
const GENERIC_CONTAINER_SPORTS = new Set(["training"]);

/** Exported so any per-activity display (ActivityCard, the type-count breakdown here) applies
 * the exact same "training" substitution rather than each inventing its own sport-label rule. */
export function displaySport(activity: ActivitySummary): string {
  if (GENERIC_CONTAINER_SPORTS.has(activity.sport) && activity.sub_sport) {
    return activity.sub_sport;
  }
  return activity.sport;
}

// The literal device-generated default `activity.name` for a sport, confirmed as the single
// overwhelmingly dominant value in the real archive (e.g. "Run" on 628 of this athlete's running
// activities, "Walk" on 201 walks) -- not a fuzzy "looks generic" guess. Sports with no single
// dominant default (cycling, training, rowing, ...) are deliberately absent, so displayActivityName
// below always just uses activity.name for those. Shared by every place that shows an activity's
// own title (ActivityCard, ActivityDetailPage) so a plain device default like "Run" never gets
// shown as if it were a real chosen title.
export const GENERIC_DEFAULT_NAME_BY_SPORT: Record<string, string> = {
  running: "Run",
  walking: "Walk",
  hiking: "Hike",
  alpine_skiing: "Ski",
  snowshoeing: "Snowshoe",
};

/** `activity.name`, unless it's still the sport's own generic device default -- in which case
 * the sport label alone already says "Run"/"Walk"/etc., so repeating it as a "title" would be
 * noise, not information. Falls back to Garmin Connect's own structured "Workout Builder" name
 * (`workout_name`, e.g. "W11 Tue - 4x2km Threshold") when one exists -- confirmed against a real
 * activity where the FIT file's own name was just the generic "Running" device default but a
 * pre-planned workout name was still real, useful information the generic-default suppression
 * above would otherwise have thrown away entirely. Matches ActivityDetailPage's own title logic
 * exactly, so the two never drift out of sync. */
export function displayActivityName(activity: ActivitySummary): string | null {
  const sport = displaySport(activity);
  const genericDefaultName = GENERIC_DEFAULT_NAME_BY_SPORT[sport];
  if (activity.name == null || activity.name === genericDefaultName) {
    return activity.workout_name ?? null;
  }
  return activity.name;
}

/** Descending by count, ties broken alphabetically for a stable, deterministic order. */
export function activityTypeCounts(activities: ActivitySummary[]): ActivityTypeCount[] {
  const counts = new Map<string, number>();
  const durations = new Map<string, number>();
  for (const a of activities) {
    const sport = displaySport(a);
    counts.set(sport, (counts.get(sport) ?? 0) + 1);
    const durationS = effectiveDurationS(a);
    if (durationS != null) {
      durations.set(sport, (durations.get(sport) ?? 0) + durationS);
    }
  }
  return Array.from(counts.entries())
    .map(([sport, count]) => ({ sport, count, durationS: durations.get(sport) ?? 0 }))
    .sort((a, b) => b.count - a.count || a.sport.localeCompare(b.sport));
}
