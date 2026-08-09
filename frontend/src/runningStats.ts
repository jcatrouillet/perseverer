// Pure computations over a flat activity list for the "<year> stats" running section.
// Deliberately built from `GET /activities` (already the pattern ActivityListPage uses for
// direct, non-rollup-backed queries) rather than a new backend endpoint -- these are one-off,
// bounded-by-a-year client-side aggregates, not a decade-spanning dashboard scan.
import type { ActivitySummary } from "./api/types";
import { parseIsoDate } from "./dateUtils";

// Elapsed time (duration_s) includes any paused/stopped time (waiting at a light, tying a
// shoe); moving time excludes it. Pace/speed should always be computed from moving time when
// it's available -- using elapsed time here was a confirmed real bug (verified against a
// second data source): a run with a long pause looks artificially slow otherwise. Falls back
// to elapsed time only when an activity genuinely has no recorded moving time.
export function effectiveDurationS(activity: ActivitySummary): number | null {
  return activity.moving_duration_s ?? activity.duration_s;
}

/** "M:SS" pace formatting. Rounding the seconds component alone (without checking for the
 * carry) was a confirmed real bug: a value like 6.999 minutes rounds its seconds to 60 and
 * prints "6:60" instead of "7:00" -- caught on the all-time view, where a wider pace range
 * makes the floating-point edge case easy to hit. */
export function formatMinPerKm(minPerKm: number): string {
  let min = Math.floor(minPerKm);
  let sec = Math.round((minPerKm - min) * 60);
  if (sec === 60) {
    min += 1;
    sec = 0;
  }
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

/** Pace over the given duration/distance, using `formatMinPerKm`'s carry-safe rounding.
 * Exported so any per-activity display (ActivityCard) computes pace the same way the running
 * stats charts/tables already do, rather than a second ad-hoc `durationS / 60 / km` inline. */
export function formatPaceMinPerKm(durationS: number, distanceM: number): string {
  if (distanceM <= 0) return "—";
  return formatMinPerKm(durationS / 60 / (distanceM / 1000));
}

// Foot sports read naturally as a pace (min/km); wheeled/oared ones read naturally as a speed
// (km/h) -- matching how Garmin Connect itself splits these, not an arbitrary per-app choice.
// Shared by ActivityCard's summary pace/speed chip and ActivityCharts' per-second stream panel,
// so the two can't disagree about which sports get which unit.
const PACE_SPORTS = new Set(["running", "walking", "hiking", "snowshoeing"]);

export function isPaceSport(sport: string): boolean {
  return PACE_SPORTS.has(sport);
}

/** A stream's raw `speed_mps` sample converted to whatever unit `sport` reads naturally in.
 * Below 0.3 m/s (slower than a ~55min/km walk) is treated as stationary, not a real pace --
 * without this floor, a runner paused at a light produces a momentary "pace" of several
 * thousand min/km that dwarfs the rest of the chart's y-axis. Matches the same threshold
 * several consumer GPS devices use for auto-pause. */
export function streamSpeedValue(sport: string, speedMps: number | null): number | null {
  if (speedMps == null) return null;
  if (isPaceSport(sport)) {
    if (speedMps < 0.3) return null;
    return 1000 / (speedMps * 60);
  }
  return speedMps * 3.6;
}

/** "1h 14m" / "42m" duration formatting -- the compact, human form used anywhere a duration is
 * a supporting stat rather than the record itself (contrast the personal-records table's exact
 * "1:14:00" clock format, which stays local to that table). */
export function formatDurationHM(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.round((totalSeconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function weekdayLabel(index: number): string {
  return WEEKDAY_LABELS[index] ?? String(index);
}

/** Monday=0 .. Sunday=6, matching this app's Monday-start convention everywhere else
 * (mondayOf/WEEKDAY_LABELS in dateUtils.ts and MonthView), not JS's native Sunday=0. */
function weekdayIndex(localDate: string): number {
  return (parseIsoDate(localDate).getUTCDay() + 6) % 7;
}

export function monthlyDistanceM(activities: ActivitySummary[]): number[] {
  const totals = new Array(12).fill(0) as number[];
  for (const a of activities) {
    if (!a.local_date || a.distance_m == null) continue;
    const month = Number(a.local_date.slice(5, 7)) - 1;
    totals[month] += a.distance_m;
  }
  return totals;
}

export interface DistanceBucket {
  label: string;
  km: number;
}

/** One bar per calendar day in [startDate, endDate] -- the month-view analog of
 * monthlyDistanceM's one-bar-per-month, since a month has too few months to bucket by but
 * plenty of individual days worth showing. */
export function distanceByDay(
  activities: ActivitySummary[],
  startDate: string,
  endDate: string,
): DistanceBucket[] {
  const byDate = dailyDistanceM(activities);
  const buckets: DistanceBucket[] = [];
  const cursor = parseIsoDate(startDate);
  const last = parseIsoDate(endDate);
  while (cursor <= last) {
    const iso = cursor.toISOString().slice(0, 10);
    buckets.push({
      label: String(cursor.getUTCDate()),
      km: Math.round((byDate.get(iso) ?? 0) / 100) / 10,
    });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return buckets;
}

/** One bar per calendar year in [startDate, endDate] -- the all-time-view analog of
 * monthlyDistanceM's one-bar-per-month: a multi-year range has too many months to bucket by
 * calendar month alone (every January across every year would collapse into one "Jan" bar). */
export function distanceByYear(
  activities: ActivitySummary[],
  startDate: string,
  endDate: string,
): DistanceBucket[] {
  const byYear = new Map<number, number>();
  for (const a of activities) {
    if (!a.local_date || a.distance_m == null) continue;
    const year = Number(a.local_date.slice(0, 4));
    byYear.set(year, (byYear.get(year) ?? 0) + a.distance_m);
  }
  const startYear = Number(startDate.slice(0, 4));
  const endYear = Number(endDate.slice(0, 4));
  const buckets: DistanceBucket[] = [];
  for (let year = startYear; year <= endYear; year++) {
    buckets.push({ label: String(year), km: Math.round((byYear.get(year) ?? 0) / 100) / 10 });
  }
  return buckets;
}

export interface ScatterPoint {
  km: number;
  pace: number;
  opacity: number;
}

/** Fades a scatter point that doesn't repeat and solidifies one that does -- a one-off effort
 * (a race, an unusual route) recedes into the background while a frequently repeated
 * distance/pace combo (a regular training loop, a standard workout) stands out. Repeats are
 * counted within a coarse bucket (nearest 1km, nearest 0.5 min/km), not exact equality, since
 * real GPS-derived distance/pace values essentially never repeat exactly even for genuinely the
 * same effort. */
export function scatterPointOpacities(points: { km: number; pace: number }[]): ScatterPoint[] {
  function bucketKey(km: number, pace: number): string {
    return `${Math.round(km)}_${Math.round(pace * 2) / 2}`;
  }
  const counts = new Map<string, number>();
  for (const p of points) {
    const key = bucketKey(p.km, p.pace);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const maxCount = Math.max(1, ...counts.values());
  return points.map((p) => {
    const count = counts.get(bucketKey(p.km, p.pace))!;
    const opacity = maxCount <= 1 ? 1 : 0.2 + 0.8 * ((count - 1) / (maxCount - 1));
    return { ...p, opacity };
  });
}

export interface WeekdayStat {
  day: number; // 0=Mon..6=Sun
  count: number;
  avgDistanceM: number;
}

export function weekdayStats(activities: ActivitySummary[]): WeekdayStat[] {
  const buckets: { count: number; distanceM: number }[] = Array.from({ length: 7 }, () => ({
    count: 0,
    distanceM: 0,
  }));
  for (const a of activities) {
    if (!a.local_date) continue;
    const bucket = buckets[weekdayIndex(a.local_date)]!;
    bucket.count += 1;
    bucket.distanceM += a.distance_m ?? 0;
  }
  return buckets.map((b, day) => ({
    day,
    count: b.count,
    avgDistanceM: b.count > 0 ? b.distanceM / b.count : 0,
  }));
}

/** Distinct calendar dates with at least one activity, sorted ascending. */
export function distinctActiveDates(activities: ActivitySummary[]): string[] {
  const dates = new Set<string>();
  for (const a of activities) if (a.local_date) dates.add(a.local_date);
  return Array.from(dates).sort();
}

export interface StreakInfo {
  longestStreakDays: number;
  longestStreakStart: string | null;
  longestStreakEnd: string | null;
  longestBreakDays: number;
  longestBreakStart: string | null;
  longestBreakEnd: string | null;
}

const MS_PER_DAY = 86_400_000;

function daysBetween(a: string, b: string): number {
  return Math.round((parseIsoDate(b).getTime() - parseIsoDate(a).getTime()) / MS_PER_DAY);
}

/** `sortedDates` must be ascending, deduplicated ISO dates (see distinctActiveDates). */
export function longestStreakAndBreak(sortedDates: string[]): StreakInfo {
  if (sortedDates.length === 0) {
    return {
      longestStreakDays: 0,
      longestStreakStart: null,
      longestStreakEnd: null,
      longestBreakDays: 0,
      longestBreakStart: null,
      longestBreakEnd: null,
    };
  }

  let longestStreak = 1;
  let longestStreakStart = sortedDates[0]!;
  let longestStreakEnd = sortedDates[0]!;
  let currentStreak = 1;
  let currentStreakStart = sortedDates[0]!;

  let longestBreak = 0;
  let longestBreakStart: string | null = null;
  let longestBreakEnd: string | null = null;

  for (let i = 1; i < sortedDates.length; i++) {
    const gap = daysBetween(sortedDates[i - 1]!, sortedDates[i]!);
    if (gap === 1) {
      currentStreak += 1;
    } else {
      currentStreak = 1;
      currentStreakStart = sortedDates[i]!;
    }
    if (currentStreak > longestStreak) {
      longestStreak = currentStreak;
      longestStreakStart = currentStreakStart;
      longestStreakEnd = sortedDates[i]!;
    }
    // A "break" is the gap of inactive days strictly between two active dates.
    const inactiveDays = gap - 1;
    if (inactiveDays > longestBreak) {
      longestBreak = inactiveDays;
      longestBreakStart = sortedDates[i - 1]!;
      longestBreakEnd = sortedDates[i]!;
    }
  }

  return {
    longestStreakDays: longestStreak,
    longestStreakStart,
    longestStreakEnd,
    longestBreakDays: longestBreak,
    longestBreakStart,
    longestBreakEnd,
  };
}

export function dailyDistanceM(activities: ActivitySummary[]): Map<string, number> {
  const byDate = new Map<string, number>();
  for (const a of activities) {
    if (!a.local_date || a.distance_m == null) continue;
    byDate.set(a.local_date, (byDate.get(a.local_date) ?? 0) + a.distance_m);
  }
  return byDate;
}

export interface DailyStat {
  distanceM: number;
  durationS: number;
  elevationGainM: number;
}

/** Same-day activities summed together, for the heatmap's per-day hover tooltip -- distance
 * alone (dailyDistanceM) isn't enough once the tooltip also shows pace and elevation. */
export function dailyStats(activities: ActivitySummary[]): Map<string, DailyStat> {
  const byDate = new Map<string, DailyStat>();
  for (const a of activities) {
    if (!a.local_date) continue;
    const existing = byDate.get(a.local_date) ?? {
      distanceM: 0,
      durationS: 0,
      elevationGainM: 0,
    };
    existing.distanceM += a.distance_m ?? 0;
    existing.durationS += a.duration_s ?? 0;
    existing.elevationGainM += a.elevation_gain_m ?? 0;
    byDate.set(a.local_date, existing);
  }
  return byDate;
}

/** The activity's local hour (0-23), derived from its own recorded UTC offset -- not the
 * browser's timezone, and not a guess: `utc_offset_s` is the same field the offset-adjusted
 * `local_date` fix (ADR 0009 decision 8) already relies on for this athlete. */
export function localHour(activity: ActivitySummary): number {
  const localMs = new Date(activity.start_time_utc).getTime() + activity.utc_offset_s * 1000;
  return new Date(localMs).getUTCHours();
}

/** "6:32 AM" -- the activity's own local start time (same utc_offset_s math as `localHour`),
 * for a per-activity card where the date is already shown by the day it's grouped under and
 * only the time of day is new information. */
export function localTimeLabel(activity: ActivitySummary): string {
  const localMs = new Date(activity.start_time_utc).getTime() + activity.utc_offset_s * 1000;
  const d = new Date(localMs);
  const hour24 = d.getUTCHours();
  const minute = d.getUTCMinutes();
  const period = hour24 < 12 ? "AM" : "PM";
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  return `${hour12}:${minute.toString().padStart(2, "0")} ${period}`;
}

export interface AmPmCounts {
  am: number;
  pm: number;
}

export function amPmCounts(activities: ActivitySummary[]): AmPmCounts {
  let am = 0;
  let pm = 0;
  for (const a of activities) {
    if (localHour(a) < 12) am += 1;
    else pm += 1;
  }
  return { am, pm };
}

// ── Heatmap encoding ────────────────────────────────────────────────────────────────────────
// Two distinct encodings for two distinct magnitudes, not one scale stretched across both: an
// ordinary cell reads as a plain blue intensity wash, while a big one instead fills a
// proportional circle -- a shape that visually pops out of the grid instead of blending into
// the same gradient as everything else.
//
// The thresholds differ by what a cell *means*. In the year/month views a cell is one day; in
// the all-time view it's a whole week, where 8km is unremarkable rather than a real run. Both
// scales below are calibrated against the actual archive, not picked for roundness.

export interface HeatmapScale {
  /** Above this, a cell switches from the gradient wash to the proportional circle. */
  shortMaxKm: number;
  /** Distance at which the circle is completely filled (and beyond, clamped). */
  fullCircleKm: number;
  gradientLegendKm: number[];
  pieLegendKm: number[];
}

/** One cell = one day. Circle starts at a 10km "long run" and completes at a marathon. */
export const DAILY_HEATMAP_SCALE: HeatmapScale = {
  shortMaxKm: 10,
  fullCircleKm: 42.195,
  gradientLegendKm: [2, 5, 8, 10],
  pieLegendKm: [15, 21.1, 30, 42.2],
};

/** One cell = one week (the all-time view). Calibrated against the real distribution of the
 * 191 weeks in the archive that contain a run: median 37km, p90 55km, max 72km. A 40km
 * boundary splits those roughly in half, so the circle still means "a big week" rather than
 * covering almost every cell; a 70km full circle is reached by the biggest week on record. */
export const WEEKLY_HEATMAP_SCALE: HeatmapScale = {
  shortMaxKm: 40,
  fullCircleKm: 70,
  gradientLegendKm: [10, 20, 30, 40],
  pieLegendKm: [50, 60, 70],
};

export function isLongRun(km: number, scale: HeatmapScale): boolean {
  return km > scale.shortMaxKm;
}

/** Background color-mix percentage below the boundary, linear in distance with a floor so even
 * a very short run stays visibly distinct from an empty "no run" cell. */
export function shortRunHeatPct(km: number, scale: HeatmapScale): number {
  if (km <= 0) return 0;
  return Math.min(92, 15 + (km / scale.shortMaxKm) * 77);
}

/** Degrees of conic-gradient fill above the boundary: 0deg right at it, growing to a full
 * 360deg at `fullCircleKm` (and beyond, clamped). */
export function longRunPieDeg(km: number, scale: HeatmapScale): number {
  const frac = (km - scale.shortMaxKm) / (scale.fullCircleKm - scale.shortMaxKm);
  return Math.max(0, Math.min(1, frac)) * 360;
}

/** Every reference distance in the legend, in ascending order. */
export function legendKm(scale: HeatmapScale): number[] {
  return [...scale.gradientLegendKm, ...scale.pieLegendKm];
}

/** Buckets an actual distance to its nearest legend reference, so hovering a legend swatch can
 * highlight the matching cells. */
export function nearestLegendKm(km: number, scale: HeatmapScale): number {
  return legendKm(scale).reduce((closest, candidate) =>
    Math.abs(candidate - km) < Math.abs(closest - km) ? candidate : closest,
  );
}

export interface StandardDistance {
  label: string;
  meters: number;
}

// Common race distances, matching the rows Strava/intervals.icu show on their own "personal
// records" pages.
export const STANDARD_DISTANCES: StandardDistance[] = [
  { label: "1 mile", meters: 1609.34 },
  { label: "3 km", meters: 3000 },
  { label: "5 km", meters: 5000 },
  { label: "4 mile", meters: 6437.38 },
  { label: "5 mile", meters: 8046.72 },
  { label: "10 km", meters: 10000 },
  { label: "15 km", meters: 15000 },
  { label: "10 mile", meters: 16093.4 },
  { label: "20 km", meters: 20000 },
  { label: "Half marathon", meters: 21097.5 },
  { label: "Marathon", meters: 42195 },
];

export interface PersonalRecord {
  label: string;
  date: string;
  actualDistanceM: number;
  durationS: number;
  paceMinPerKm: number;
  speedKmh: number;
  eligibleCount: number;
}

/** An honest approximation, not Strava/intervals.icu's real "best effort" feature: that
 * extracts the fastest continuous segment of exactly the target distance from every activity's
 * full per-second stream (a sliding-window analysis this project doesn't implement anywhere
 * yet, and a much bigger feature than a stats page needs). Instead, for each standard
 * distance, this picks the fastest *whole recorded activity* within a tolerance band of that
 * distance -- real data, just a coarser match than a true best-effort. */
export function personalRecords(activities: ActivitySummary[]): PersonalRecord[] {
  const records: PersonalRecord[] = [];
  for (const std of STANDARD_DISTANCES) {
    const minM = std.meters * 0.9;
    const maxM = std.meters * 1.3;
    const eligible = activities.filter(
      (a) => a.distance_m != null && a.distance_m >= minM && a.distance_m <= maxM && effectiveDurationS(a) != null,
    );
    if (eligible.length === 0) continue;
    const best = eligible.reduce((fastest, a) =>
      effectiveDurationS(a)! / a.distance_m! < effectiveDurationS(fastest)! / fastest.distance_m!
        ? a
        : fastest,
    );
    const distanceKm = best.distance_m! / 1000;
    const bestDurationS = effectiveDurationS(best)!;
    records.push({
      label: std.label,
      date: best.local_date ?? best.start_time_utc.slice(0, 10),
      actualDistanceM: best.distance_m!,
      durationS: bestDurationS,
      paceMinPerKm: bestDurationS / 60 / distanceKm,
      speedKmh: distanceKm / (bestDurationS / 3600),
      eligibleCount: eligible.length,
    });
  }
  return records;
}

export interface RollingPoint {
  local_date: string;
  distanceM: number;
}

/** A rolling `windowDays`-trailing sum of distance, one point per date from `seriesStart` to
 * `seriesEnd` inclusive. The window can't see activity before `seriesStart` (we only fetch one
 * year), so early points understate the true trailing total -- an accepted, documented
 * simplification rather than fetching a second year of data just to prime the window. */
export function rollingDistanceKm(
  activities: ActivitySummary[],
  seriesStart: string,
  seriesEnd: string,
  windowDays: number,
): RollingPoint[] {
  const byDate = dailyDistanceM(activities);

  const dates: string[] = [];
  const cursor = parseIsoDate(seriesStart);
  const last = parseIsoDate(seriesEnd);
  while (cursor <= last) {
    dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }

  const points: RollingPoint[] = [];
  let windowSum = 0;
  const window: number[] = [];
  for (const date of dates) {
    const todayM = byDate.get(date) ?? 0;
    window.push(todayM);
    windowSum += todayM;
    if (window.length > windowDays) {
      windowSum -= window.shift()!;
    }
    points.push({ local_date: date, distanceM: windowSum });
  }
  return points;
}
