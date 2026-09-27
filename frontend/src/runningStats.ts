// Pure computations over a flat activity list for the "<year> stats" running section.
// Deliberately built from `GET /activities` (already the pattern ActivityListPage uses for
// direct, non-rollup-backed queries) rather than a new backend endpoint -- these are one-off,
// bounded-by-a-year client-side aggregates, not a decade-spanning dashboard scan.
import type { ActivitySummary, TimeFormat } from "./api/types";
import { isoDate, mondayOf, parseIsoDate, type WeekStartDay } from "./dateUtils";
import { formatClock } from "./formatTime";

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

/** m/s -> min/km, for the handful of places that read a server-computed `avg_gap_speed_mps`
 * (whole-activity via GET .../comparisons, per-lap via GET /activities/{id}'s own laps -- see
 * gap.py) and need to format it the same way as any other pace. */
export function gapPaceMinPerKm(avgGapSpeedMps: number): number {
  return 1000 / (avgGapSpeedMps * 60);
}

// Foot sports read naturally as a pace (min/km); wheeled/oared ones read naturally as a speed
// (km/h) -- matching how Garmin Connect itself splits these, not an arbitrary per-app choice.
// Shared by ActivityCard's summary pace/speed chip and ActivityCharts' per-second stream panel,
// so the two can't disagree about which sports get which unit.
const PACE_SPORTS = new Set(["running", "walking", "hiking", "snowshoeing"]);

export function isPaceSport(sport: string): boolean {
  return PACE_SPORTS.has(sport);
}

// Mirrors the backend's merge/engine.py::sport_family "run" bucket exactly -- duplicated rather
// than shared cross-language, same precedent as personalRecords/rules_pb.py (ADR 0012). Used to
// gate the per-activity "run insights" panel, which is deliberately running-specific (see
// ActivityDetailPage.tsx) even though the backend endpoint itself is sport-agnostic.
const RUNNING_SPORTS = new Set([
  "running",
  "trail_running",
  "treadmill_running",
  "track_running",
  "street_running",
]);

export function isRunningSport(sport: string): boolean {
  return RUNNING_SPORTS.has(sport);
}

// Below this speed, a sample is "stopped" (a traffic light, tying a shoe, a device auto-pause
// boundary), not a genuinely slow moving pace -- matches several consumer GPS devices' own
// auto-pause threshold, and the backend's identical `_STATIONARY_MPS_FLOOR` (pace_bands.py,
// gap.py). Exported so any consumer needing "was this sample actually moving" (not just the
// pace-sport-specific display floor in `streamSpeedValue` below) can use the same value --
// see `computeHrZonesFromStream`'s own use of it for why: a stopped interval must not count
// toward moving time, the same way `effectiveDurationS` already excludes it elsewhere.
export const STATIONARY_MPS_FLOOR = 0.3;

/** A stream's raw `speed_mps` sample converted to whatever unit `sport` reads naturally in.
 * Below the stationary floor is treated as stationary, not a real pace -- without this floor, a
 * runner paused at a light produces a momentary "pace" of several thousand min/km that dwarfs
 * the rest of the chart's y-axis. */
export function streamSpeedValue(sport: string, speedMps: number | null): number | null {
  if (speedMps == null) return null;
  if (isPaceSport(sport)) {
    if (speedMps < STATIONARY_MPS_FLOOR) return null;
    return 1000 / (speedMps * 60);
  }
  return speedMps * 3.6;
}

/** Nulls out isolated samples that differ wildly from their immediate neighbors -- a brief GPS/
 * footpod glitch or a momentary near-stop (crossing a road, a red light) mid-activity, not a
 * genuine change in pace. Confirmed against real data: Garmin Connect's own pace graph excludes
 * exactly these single-point outliers rather than plotting (and auto-scaling the whole chart
 * around) them, while still showing a genuinely sustained slow stretch (e.g. a real recovery
 * jog) in full. Operates on raw speed (m/s) rather than pace -- a near-zero-speed glitch would
 * otherwise explode into an enormous pace number, so working in speed units catches a
 * slow-direction and a fast-direction (e.g. a GPS jump) glitch symmetrically with one threshold.
 * A *local* window (not the whole series' median) is essential: a long steady interval at a
 * genuinely different pace from the rest of the activity must not itself be flagged just because
 * it differs from the global median. */
export function rejectSpeedOutliers(values: (number | null)[]): (number | null)[] {
  const WINDOW = 4;
  const MIN_NEIGHBORS = 3;
  const MIN_THRESHOLD_MPS = 0.6;
  const MAD_MULTIPLIER = 4;

  return values.map((v, i) => {
    if (v == null) return v;
    const neighbors: number[] = [];
    for (let j = Math.max(0, i - WINDOW); j <= Math.min(values.length - 1, i + WINDOW); j++) {
      if (j === i) continue;
      const nv = values[j];
      if (nv != null) neighbors.push(nv);
    }
    if (neighbors.length < MIN_NEIGHBORS) return v;
    const sorted = [...neighbors].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)]!;
    const deviations = sorted.map((n) => Math.abs(n - median)).sort((a, b) => a - b);
    const mad = deviations[Math.floor(deviations.length / 2)]!;
    const threshold = Math.max(MIN_THRESHOLD_MPS, MAD_MULTIPLIER * mad);
    return Math.abs(v - median) > threshold ? null : v;
  });
}

/** A device pause found in a stream's own per-sample elapsed times: a gap between two
 * consecutive recorded samples far larger than the stream's typical sample interval.
 * `postGapIndex` is the index of the first sample recorded after the pause -- that sample's own
 * value is suspect too (GPS reacquisition / stride restart, the same category of artifact as the
 * very first sample of the whole activity), not just the gap itself. */
export interface PauseGap {
  postGapIndex: number;
  gapSeconds: number;
}

export interface PauseDetection {
  gaps: PauseGap[];
  /** The stream's typical (median) sample interval, in seconds -- also what a detected pause
   * compresses down to, so a compressed gap still advances the x-axis a little rather than
   * jumping instantaneously, matching how an ordinary (non-paused) gap between samples looks. */
  typicalIntervalS: number;
}

/** Finds real device pauses/stops from a stream's own per-sample elapsed times -- a gap far
 * larger than the stream's own typical spacing. A *local, per-stream* threshold (not a fixed
 * number of seconds) is essential: different tiers/activities have different typical sample
 * spacing, so what counts as "way bigger than normal" has to be relative to this stream, not an
 * arbitrary constant. */
export function detectPauseGaps(rawElapsed: number[]): PauseDetection {
  const deltas: number[] = [];
  for (let i = 1; i < rawElapsed.length; i++) {
    const d = rawElapsed[i]! - rawElapsed[i - 1]!;
    if (d > 0) deltas.push(d);
  }
  if (deltas.length === 0) return { gaps: [], typicalIntervalS: 1 };
  const sorted = [...deltas].sort((a, b) => a - b);
  const typicalIntervalS = sorted[Math.floor(sorted.length / 2)]!;
  const threshold = Math.max(30, typicalIntervalS * 4);
  const gaps: PauseGap[] = [];
  for (let i = 1; i < rawElapsed.length; i++) {
    const gap = rawElapsed[i]! - rawElapsed[i - 1]!;
    if (gap > threshold) gaps.push({ postGapIndex: i, gapSeconds: gap });
  }
  return { gaps, typicalIntervalS };
}

/** Turns wall-clock elapsed time into "moving" elapsed time by subtracting every detected pause
 * that occurred before a given point -- the same concept as activity.moving_duration_s
 * (effectiveDurationS above), just applied per-second to a chart's x-axis instead of once to a
 * whole activity's summary stat. A pause isn't erased to a single instant -- it collapses to one
 * typical sample interval, matching how an ordinary (non-paused) gap between two samples already
 * looks, rather than creating a suspicious zero-width jump. Returns the identity function when
 * there's nothing to compress, so callers can apply it unconditionally. */
export function buildPauseCompressor(
  rawElapsed: number[],
  detection: PauseDetection,
): (rawT: number) => number {
  if (detection.gaps.length === 0) return (t) => t;
  let totalCut = 0;
  const cutPoints = detection.gaps.map((g) => {
    totalCut += g.gapSeconds - detection.typicalIntervalS;
    return { at: rawElapsed[g.postGapIndex]!, subtract: totalCut };
  });
  return (rawT: number) => {
    let subtract = 0;
    for (const cp of cutPoints) {
      if (rawT >= cp.at) subtract = cp.subtract;
      else break;
    }
    return rawT - subtract;
  };
}

/** "1h 14m" / "42m" duration formatting -- the compact, human form used anywhere a duration is
 * a supporting stat rather than the record itself (contrast the personal-records table's exact
 * "1:14:00" clock format, which stays local to that table). */
export function formatDurationHM(totalSeconds: number): string {
  // Round to whole minutes first, so 59m 50s reads "1h 0m" rather than "60m".
  const totalMinutes = Math.round(totalSeconds / 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** "1:14:23" / "3:45" clock-format duration, precise to the second -- for anywhere a duration
 * needs to stay legible at both interval-training scale (a 45s rep must not round away to "1m",
 * formatDurationHM's granularity) and multi-hour scale in the same column (e.g. an activity with
 * a single lap spanning its whole multi-hour duration). Promoted out of ActivityCharts.tsx's
 * private x-axis formatter for reuse by the interval table, which had the same real needs. */
export function formatClockDuration(totalSeconds: number): string {
  const s = Math.round(totalSeconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`
    : `${m}:${sec.toString().padStart(2, "0")}`;
}

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function weekdayLabel(index: number): string {
  return WEEKDAY_LABELS[index] ?? String(index);
}

/** Index of `localDate` within its own display week, per `weekStartDay` (default Monday=0, the
 * app's own long-standing convention, not JS's native Sunday=0). Sunday-start is `getUTCDay()`
 * directly (already 0=Sun..6=Sat). Used for calendar-grid/heatmap column placement. */
export function weekdayIndex(localDate: string, weekStartDay: WeekStartDay = "monday"): number {
  const day = parseIsoDate(localDate).getUTCDay();
  return weekStartDay === "monday" ? (day + 6) % 7 : day;
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
export function scatterPointOpacities<T extends { km: number; pace: number }>(
  points: T[],
): (T & { opacity: number })[] {
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

/** "6:32 AM" / "06:32" -- the activity's own local start time (same utc_offset_s math as
 * `localHour`), for a per-activity card where the date is already shown by the day it's grouped
 * under and only the time of day is new information. `format` is the athlete's own Personalize
 * time-format preference (useTimeFormat()), 12h to match this function's own historical default
 * when a caller doesn't pass one. */
export function localTimeLabel(activity: ActivitySummary, format: TimeFormat = "12h"): string {
  const localMs = new Date(activity.start_time_utc).getTime() + activity.utc_offset_s * 1000;
  const d = new Date(localMs);
  return formatClock(d.getUTCHours(), d.getUTCMinutes(), format);
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
  activityId: string;
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
      activityId: best.id,
      actualDistanceM: best.distance_m!,
      durationS: bestDurationS,
      paceMinPerKm: bestDurationS / 60 / distanceKm,
      speedKmh: distanceKm / (bestDurationS / 3600),
      eligibleCount: eligible.length,
    });
  }
  return records;
}

/** Which of `periodRecords` (personalRecords() over some period-scoped activity array) are
 * *genuine* all-time bests, not just the fastest within that narrower slice -- determined by
 * matching `(label, date)` against `allTimeRecords` (personalRecords() over the athlete's
 * entire running history). A period record and its all-time counterpart are the same real
 * effort exactly when they share both the distance label and the date; a coincidentally
 * identical pace on a different date is not a match. Phase 7 "PBs set" recap ingredient, see
 * ADR 0011 decision 3. */
export function newAllTimePrs(
  periodRecords: PersonalRecord[],
  allTimeRecords: PersonalRecord[],
): PersonalRecord[] {
  const allTimeDateByLabel = new Map(allTimeRecords.map((r) => [r.label, r.date]));
  return periodRecords.filter((r) => allTimeDateByLabel.get(r.label) === r.date);
}

export interface BestVdot {
  value: number;
  date: string;
  activityId: string;
}

/** The best (highest) VDOT among `activities` that have one -- see performance.py's own
 * docstring for how it's computed and why a raw per-day VDOT trend would be misleading (an easy
 * day scores low purely from intensity, not fitness). A period's *best* value reads the way a
 * "fastest split"/PB stat already does elsewhere on this page: the strongest performance you
 * actually produced in this window, not a trend line that dips on rest days. */
export function bestVdot(activities: ActivitySummary[]): BestVdot | null {
  let best: BestVdot | null = null;
  for (const a of activities) {
    if (a.vdot == null) continue;
    if (best == null || a.vdot > best.value) {
      best = { value: a.vdot, date: a.local_date ?? a.start_time_utc.slice(0, 10), activityId: a.id };
    }
  }
  return best;
}

export interface WeeklyDistancePoint {
  weekStart: string;
  km: number;
}

/** One bucket per Monday-starting ISO week from `startDate` through `endDate`, summing distance
 * across all `activities` that land in it -- the week-over-time counterpart to
 * distanceByDay/distanceByYear, used for a "weekly distance over the last N months" bar chart. */
export function weeklyDistanceSeries(
  activities: ActivitySummary[],
  startDate: string,
  endDate: string,
): WeeklyDistancePoint[] {
  const byWeek = new Map<string, number>();
  for (const a of activities) {
    if (!a.local_date || a.distance_m == null) continue;
    const monday = isoDate(mondayOf(parseIsoDate(a.local_date)));
    byWeek.set(monday, (byWeek.get(monday) ?? 0) + a.distance_m);
  }
  const points: WeeklyDistancePoint[] = [];
  const cursor = mondayOf(parseIsoDate(startDate));
  const last = parseIsoDate(endDate);
  while (cursor <= last) {
    const iso = isoDate(cursor);
    points.push({ weekStart: iso, km: Math.round((byWeek.get(iso) ?? 0) / 100) / 10 });
    cursor.setUTCDate(cursor.getUTCDate() + 7);
  }
  return points;
}

export interface WeeklyBestVdotPoint {
  weekStart: string;
  vdot: number | null;
  activityId: string | null;
}

/** One bucket per Monday-starting ISO week from `startDate` through `endDate`, each holding the
 * *best* (highest) VDOT among that week's activities -- the week-over-time counterpart to
 * `weeklyDistanceSeries` above, same "best effort, not a trend line" reasoning as `bestVdot`.
 * `vdot`/`activityId` are both null for a week with no VDOT-eligible run, not 0 -- an empty week
 * has no performance to report, not a zero one. */
export function weeklyBestVdotSeries(
  activities: ActivitySummary[],
  startDate: string,
  endDate: string,
): WeeklyBestVdotPoint[] {
  const byWeek = new Map<string, { vdot: number; activityId: string }>();
  for (const a of activities) {
    if (!a.local_date || a.vdot == null) continue;
    const monday = isoDate(mondayOf(parseIsoDate(a.local_date)));
    const current = byWeek.get(monday);
    if (current == null || a.vdot > current.vdot) {
      byWeek.set(monday, { vdot: a.vdot, activityId: a.id });
    }
  }
  const points: WeeklyBestVdotPoint[] = [];
  const cursor = mondayOf(parseIsoDate(startDate));
  const last = parseIsoDate(endDate);
  while (cursor <= last) {
    const iso = isoDate(cursor);
    const entry = byWeek.get(iso);
    points.push({
      weekStart: iso,
      vdot: entry ? Math.round(entry.vdot * 10) / 10 : null,
      activityId: entry?.activityId ?? null,
    });
    cursor.setUTCDate(cursor.getUTCDate() + 7);
  }
  return points;
}

export interface VdotTrendPoint {
  id: string;
  /** Epoch ms (UTC midnight of local_date) -- for a numeric time x-axis, matching
   * healthStats.ts::MergedTrendPoint's own `ts` convention. */
  ts: number;
  localDate: string;
  vdot: number;
  isRace: boolean;
  /** This run's own VDOT was the highest of any run in its Monday-starting ISO week -- the
   * same "best effort, not a trend line" concept weeklyBestVdotSeries already buckets down to
   * one point per week, kept here at the individual-run level instead so every run stays
   * visible and the performance frontier can be traced as a line through just these points. */
  isWeeklyBest: boolean;
  durationS: number | null;
}

/** One point per VDOT-eligible running activity -- the scatter data behind the Insights "Pace
 * trends" chart (a run's own VDOT plotted over time, with the weekly-best-effort runs and race
 * runs called out from the rest, per the same reasoning as bestVdot/weeklyBestVdotSeries
 * above). Sorted chronologically so a caller can draw a line through the isWeeklyBest subset
 * without re-sorting. */
export function vdotTrendPoints(activities: ActivitySummary[]): VdotTrendPoint[] {
  const bestIdByWeek = new Map<string, { vdot: number; id: string }>();
  for (const a of activities) {
    if (!a.local_date || a.vdot == null) continue;
    const monday = isoDate(mondayOf(parseIsoDate(a.local_date)));
    const current = bestIdByWeek.get(monday);
    if (current == null || a.vdot > current.vdot) {
      bestIdByWeek.set(monday, { vdot: a.vdot, id: a.id });
    }
  }
  const bestIds = new Set([...bestIdByWeek.values()].map((v) => v.id));

  const points: VdotTrendPoint[] = [];
  for (const a of activities) {
    if (!a.local_date || a.vdot == null) continue;
    points.push({
      id: a.id,
      ts: parseIsoDate(a.local_date).getTime(),
      localDate: a.local_date,
      vdot: Math.round(a.vdot * 10) / 10,
      isRace: a.is_race === true,
      isWeeklyBest: bestIds.has(a.id),
      durationS: effectiveDurationS(a),
    });
  }
  return points.sort((a, b) => a.ts - b.ts);
}

// Above this, a "running"-tagged activity's own pace is implausible for actual running --
// walking/hiking speed, not a genuinely slow run. Calibrated against the real archive, not
// picked arbitrarily: across 635 real running activities, the slowest genuine run is 7.6
// min/km and the next-slowest is 10.9 min/km -- a wide, clean gap with nothing in between, so
// 8.5 min/km sits safely in the middle. These are activities the device/export itself tagged
// sport=running (verified: real GPS tracks, ~3.4 km/h average speed, mountain altitude --
// hikes, not corrupted data) -- excluding them from aggregate running comparisons never
// touches the activity's own stored sport classification, which stays exactly what was
// recorded; it only keeps derived views (pace-vs-distance, weekly-distance totals) from being
// skewed by a handful of real hikes wearing the wrong label.
const IMPLAUSIBLE_RUN_PACE_MIN_PER_KM = 8.5;

/** True if `durationS`/`distanceM` describes a pace plausible for actual running (see
 * IMPLAUSIBLE_RUN_PACE_MIN_PER_KM's own comment for how the cutoff was chosen). */
export function isPlausibleRunPace(durationS: number, distanceM: number): boolean {
  if (distanceM <= 0) return false;
  return durationS / 60 / (distanceM / 1000) <= IMPLAUSIBLE_RUN_PACE_MIN_PER_KM;
}

/** MET-minutes for one activity, the standard gross-MET approximation used by WHO/CDC physical-
 * activity guidelines and most consumer fitness platforms: 1 MET-hour of *gross* energy
 * expenditure (i.e. including resting metabolism, which is what Garmin's own `calories` already
 * is) burns approximately 1 kcal per kg of body weight, so MET-hours = calories / weight_kg,
 * and MET-minutes = that * 60. Not a reproduction of any specific vendor's internal MET
 * calculation (which may use net/active calories or a different constant) -- an honest,
 * standard-formula approximation from this activity's own real calories and the athlete's own
 * recorded weight at the time (fit.user_profile.weight), same posture as Fitness & Form's
 * independently-computed CTL/ATL/TSB. */
export function metMinutes(calories: number, weightKg: number): number {
  return (calories / weightKg) * 60;
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
