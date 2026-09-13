// Resolution + time-window navigation shared by the Fitness & Form and Health tabs' trend
// charts (components/TrendChart.tsx, components/TrendControls.tsx, pages/FitnessPage.tsx,
// pages/HealthPage.tsx). Each resolution buckets an already-fetched daily series to a coarser
// grain and shows one fixed-size window of buckets the athlete steps through:
//
//   week  -> one bucket per day,   window = one Monday-Sunday week, steps by 1 week
//   month -> one bucket per week,  window = one calendar month,     steps by 1 month
//   year  -> one bucket per month, window = one calendar year,      steps by 1 year
//   all   -> one bucket per month, window = the entire available data range, no navigation
//
// Both tabs fetch their full history once (not per-window) and do all of this bucketing/
// windowing client-side -- there is no week/month/year rollup table for fitness_daily_rollup or
// health_metric_daily_rollup (confirmed: only day and week/month period_rollup exist, no year/
// all-time rollup anywhere), and a daily series over the app's real history (~1400 days) is
// cheap enough to hold in memory and slice/average on every navigation click with zero extra
// network round-trips.
import {
  EARLIEST_PLAUSIBLE_DATE,
  isoDate,
  monthName,
  monthRange,
  mondayOf,
  parseIsoDate,
  weekRange,
  yearRange,
} from "./dateUtils";

export type Resolution = "week" | "month" | "year" | "all" | "custom";
export type BucketBy = "day" | "week" | "month";

/** An explicit start/end the athlete picked directly, only meaningful when resolution="custom".
 * Bucketed by day/week/month depending on the span, same three-tier threshold RunningStats.tsx
 * already uses for its own three real display modes (spanDays <= 31 / > 366 / else) -- reused
 * here rather than invented fresh, so a two-week custom range and a two-week "Week" navigation
 * read the same way, and a three-year custom range reads like "All time" does. */
export interface CustomRange {
  start: string;
  end: string;
}

/** One day's worth of values for however many series keys the caller cares about -- the shape
 * both `FitnessDailyRollupOut[]` (after a small adapter) and `mergeTrendSeries`'s own
 * `MergedTrendPoint[]` already satisfy. */
export interface DailyPoint {
  local_date: string;
  [seriesKey: string]: string | number | null;
}

/** One bucket's worth of averaged values, keyed by series -- what `TrendChart` plots directly. */
export interface TrendPoint {
  /** Display label for this bucket's x-axis tick, e.g. "Mon 25", "Aug 25", "Jan", "Jan 2024". */
  x: string;
  /** Bucket-start epoch ms -- not used for chart positioning (the x-axis is a category axis,
   * every bucket gets equal width regardless of its real duration) but kept for stable sort
   * order and as a React key. */
  ts: number;
  [seriesKey: string]: string | number | null;
}

export interface TrendWindow {
  resolution: Resolution;
  bucketBy: BucketBy;
  /** ISO date bounds, inclusive -- the range of local_dates this window covers. */
  start: string;
  end: string;
  /** One entry per bucket shown, in display order -- `bucketKeys[i]` identifies the bucket
   * (a local_date for "day", a Monday for "week", "YYYY-MM" for "month") and `bucketLabels[i]`
   * is its display label. Always non-empty. */
  bucketKeys: string[];
  bucketLabels: string[];
  /** Window-level heading, e.g. "Aug 25 - Sep 1, 2026" / "September 2026" / "2026" / "All time". */
  label: string;
  canGoPrevious: boolean;
  canGoNext: boolean;
}

const SHORT_MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];
const SHORT_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function shortDayLabel(iso: string): string {
  const d = parseIsoDate(iso);
  const weekday = SHORT_WEEKDAY_NAMES[(d.getUTCDay() + 6) % 7]; // Sunday=0 -> index 6
  return `${weekday} ${d.getUTCDate()}`;
}

function shortMonthDayLabel(iso: string): string {
  const d = parseIsoDate(iso);
  return `${SHORT_MONTH_NAMES[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

function shortMonthLabel(yearMonth: string): string {
  const month = Number(yearMonth.slice(5, 7));
  return SHORT_MONTH_NAMES[month - 1] ?? yearMonth;
}

function monthYearLabel(yearMonth: string): string {
  const year = yearMonth.slice(0, 4);
  const month = Number(yearMonth.slice(5, 7));
  return `${SHORT_MONTH_NAMES[month - 1] ?? ""} ${year}`;
}

function formatDateRangeLabel(start: string, end: string): string {
  const s = parseIsoDate(start);
  const e = parseIsoDate(end);
  const sameYear = s.getUTCFullYear() === e.getUTCFullYear();
  const startLabel = `${SHORT_MONTH_NAMES[s.getUTCMonth()]} ${s.getUTCDate()}`;
  const endLabel = sameYear
    ? `${SHORT_MONTH_NAMES[e.getUTCMonth()]} ${e.getUTCDate()}`
    : `${SHORT_MONTH_NAMES[e.getUTCMonth()]} ${e.getUTCDate()}, ${e.getUTCFullYear()}`;
  return `${startLabel} - ${endLabel}, ${e.getUTCFullYear()}`;
}

function addDays(iso: string, days: number): string {
  const d = parseIsoDate(iso);
  d.setUTCDate(d.getUTCDate() + days);
  return isoDate(d);
}

function addMonthsToFirstOfMonth(year: number, month1to12: number, delta: number): { year: number; month: number } {
  const total = (year * 12 + (month1to12 - 1)) + delta;
  return { year: Math.floor(total / 12), month: (total % 12) + 1 };
}

function enumerateDailyBuckets(start: string, end: string): { key: string; label: string }[] {
  const out: { key: string; label: string }[] = [];
  let cursor = start;
  while (cursor <= end) {
    out.push({ key: cursor, label: shortDayLabel(cursor) });
    cursor = addDays(cursor, 1);
  }
  return out;
}

function enumerateWeeklyBuckets(start: string, end: string): { key: string; label: string }[] {
  const out: { key: string; label: string }[] = [];
  let cursor = isoDate(mondayOf(parseIsoDate(start)));
  while (cursor <= end) {
    out.push({ key: cursor, label: shortMonthDayLabel(cursor) });
    cursor = addDays(cursor, 7);
  }
  return out;
}

function enumerateMonthlyBuckets(
  start: string,
  end: string,
  labelFn: (yearMonth: string) => string,
): { key: string; label: string }[] {
  const out: { key: string; label: string }[] = [];
  let year = Number(start.slice(0, 4));
  let month = Number(start.slice(5, 7));
  const endKey = end.slice(0, 7);
  while (`${year}-${String(month).padStart(2, "0")}` <= endKey) {
    const key = `${year}-${String(month).padStart(2, "0")}`;
    out.push({ key, label: labelFn(key) });
    const next = addMonthsToFirstOfMonth(year, month, 1);
    year = next.year;
    month = next.month;
  }
  return out;
}

/** The earliest local_date across every point the caller has fetched, or `EARLIEST_PLAUSIBLE_DATE`
 * as a fallback so an athlete with genuinely zero data yet still gets a well-formed (if empty)
 * "all time" window rather than a crash. */
export function earliestDate(points: { local_date: string }[]): string {
  let earliest: string | null = null;
  for (const p of points) {
    if (earliest === null || p.local_date < earliest) earliest = p.local_date;
  }
  return earliest ?? EARLIEST_PLAUSIBLE_DATE;
}

/** Like `earliestDate`, but scoped to whichever of `points` actually carries a value for one of
 * `keys` -- a page merges several unrelated metrics into one fetched series (e.g. HealthPage's
 * weight/sleep/steps/etc. all share one merged `DailyPoint[]`), and each metric's own "All time"
 * view must start where *that metric's* data starts, not wherever the single oldest metric on
 * the whole page happens to begin (e.g. sleep data from 2022 showing an "All time" x-axis
 * stretching back to a 2016 weight reading it has nothing to do with). */
export function earliestDateForKeys(points: DailyPoint[], keys: string[]): string {
  return earliestDate(points.filter((p) => keys.some((k) => typeof p[k] === "number")));
}

/** A sensible default range to seed resolution="custom" with the moment the athlete switches to
 * it -- the last 30 days, clamped to not start before the athlete's own earliest data. */
export function defaultCustomRange(dataStart: string, today: string): CustomRange {
  const start = addDays(today, -29);
  return { start: start > dataStart ? start : dataStart, end: today };
}

const MS_PER_DAY = 86_400_000;

/** Builds the window for `resolution`, anchored on any date within it -- `anchor` need not be
 * the window's own start (e.g. "next month" just adds a month to the current anchor and this
 * function re-derives that month's own real start/end). `dataStart`/`today` bound how far
 * `canGoPrevious`/`canGoNext` allow navigating -- never past the athlete's own earliest data,
 * never into the future. `customRange` is only consulted for resolution="custom"; its bucket
 * granularity (day/week/month) is picked from its own span using the exact same thresholds
 * RunningStats.tsx already uses for its three real display modes. */
export function computeWindow(
  resolution: Resolution,
  anchor: string,
  dataStart: string,
  today: string,
  customRange?: CustomRange,
): TrendWindow {
  if (resolution === "custom" && customRange) {
    const { start, end } = customRange;
    const spanDays =
      Math.round((parseIsoDate(end).getTime() - parseIsoDate(start).getTime()) / MS_PER_DAY) + 1;
    let bucketBy: BucketBy;
    let buckets: { key: string; label: string }[];
    if (spanDays <= 31) {
      bucketBy = "day";
      buckets = enumerateDailyBuckets(start, end);
    } else if (spanDays > 366) {
      bucketBy = "month";
      buckets = enumerateMonthlyBuckets(start, end, monthYearLabel);
    } else {
      bucketBy = "week";
      buckets = enumerateWeeklyBuckets(start, end);
    }
    return {
      resolution, bucketBy, start, end,
      bucketKeys: buckets.map((b) => b.key),
      bucketLabels: buckets.map((b) => b.label),
      label: formatDateRangeLabel(start, end),
      // A custom range has no "previous"/"next" unit to step by -- same as "all time".
      canGoPrevious: false,
      canGoNext: false,
    };
  }
  if (resolution === "week") {
    const { start, end } = weekRange(anchor);
    const buckets = enumerateDailyBuckets(start, end);
    return {
      resolution, bucketBy: "day", start, end,
      bucketKeys: buckets.map((b) => b.key),
      bucketLabels: buckets.map((b) => b.label),
      label: formatDateRangeLabel(start, end),
      canGoPrevious: start > dataStart,
      canGoNext: end < today,
    };
  }
  if (resolution === "month") {
    const d = parseIsoDate(anchor);
    const year = d.getUTCFullYear();
    const month = d.getUTCMonth() + 1;
    const { start, end } = monthRange(year, month);
    const buckets = enumerateWeeklyBuckets(start, end);
    return {
      resolution, bucketBy: "week", start, end,
      bucketKeys: buckets.map((b) => b.key),
      bucketLabels: buckets.map((b) => b.label),
      label: `${monthName(month)} ${year}`,
      canGoPrevious: start > dataStart,
      canGoNext: end < today,
    };
  }
  if (resolution === "year") {
    const year = parseIsoDate(anchor).getUTCFullYear();
    const { start, end } = yearRange(year);
    const buckets = enumerateMonthlyBuckets(start, end, shortMonthLabel);
    return {
      resolution, bucketBy: "month", start, end,
      bucketKeys: buckets.map((b) => b.key),
      bucketLabels: buckets.map((b) => b.label),
      label: String(year),
      canGoPrevious: start > dataStart,
      canGoNext: end < today,
    };
  }
  // "all" -- the entire available range, no navigation.
  const start = dataStart;
  const end = today;
  const buckets = enumerateMonthlyBuckets(start, end, monthYearLabel);
  return {
    resolution, bucketBy: "month", start, end,
    bucketKeys: buckets.map((b) => b.key),
    bucketLabels: buckets.map((b) => b.label),
    label: "All time",
    canGoPrevious: false,
    canGoNext: false,
  };
}

/** The anchor date to pass back into `computeWindow` after stepping one unit forward/back --
 * `direction` is +1 (next/newer) or -1 (previous/older). No-op for "all" (nothing to step). */
export function shiftAnchor(window: TrendWindow, direction: 1 | -1): string {
  if (window.resolution === "week") {
    return addDays(window.start, 7 * direction);
  }
  if (window.resolution === "month") {
    const year = Number(window.start.slice(0, 4));
    const month = Number(window.start.slice(5, 7));
    const next = addMonthsToFirstOfMonth(year, month, direction);
    return `${next.year}-${String(next.month).padStart(2, "0")}-01`;
  }
  if (window.resolution === "year") {
    const year = Number(window.start.slice(0, 4)) + direction;
    return `${year}-01-01`;
  }
  // "all" and "custom" -- nothing to step, no-op (custom has no unit of navigation, all is the
  // entire available range already).
  return window.start;
}

function bucketKeyForDate(date: string, bucketBy: BucketBy): string {
  if (bucketBy === "day") return date;
  if (bucketBy === "week") return isoDate(mondayOf(parseIsoDate(date)));
  return date.slice(0, 7);
}

function bucketTimestamp(bucketKey: string, bucketBy: BucketBy): number {
  if (bucketBy === "month") {
    return Date.UTC(Number(bucketKey.slice(0, 4)), Number(bucketKey.slice(5, 7)) - 1, 1);
  }
  return new Date(`${bucketKey}T00:00:00Z`).getTime();
}

/** Averages `points` (already-fetched daily data, of any span) into the fixed set of buckets
 * `window` describes, for each of `keys` independently -- a bucket with no real observations
 * for a given key gets `null` for it (not 0, not omitted), so a gap reads as a real gap on the
 * chart rather than a false dip to zero. Values outside `window.start`/`end` are ignored, so
 * the caller can safely pass its entire fetched history for every window/resolution. */
export function bucketSeriesToWindow(
  points: DailyPoint[],
  keys: string[],
  window: TrendWindow,
): TrendPoint[] {
  const sums = new Map<string, Map<string, number>>();
  const counts = new Map<string, Map<string, number>>();

  for (const p of points) {
    if (p.local_date < window.start || p.local_date > window.end) continue;
    const bucketKey = bucketKeyForDate(p.local_date, window.bucketBy);
    for (const key of keys) {
      const value = p[key];
      if (typeof value !== "number") continue;
      const sumMap = sums.get(bucketKey) ?? new Map<string, number>();
      const countMap = counts.get(bucketKey) ?? new Map<string, number>();
      sumMap.set(key, (sumMap.get(key) ?? 0) + value);
      countMap.set(key, (countMap.get(key) ?? 0) + 1);
      sums.set(bucketKey, sumMap);
      counts.set(bucketKey, countMap);
    }
  }

  return window.bucketKeys.map((bucketKey, i) => {
    const point: TrendPoint = {
      x: window.bucketLabels[i]!,
      ts: bucketTimestamp(bucketKey, window.bucketBy),
    };
    const sumMap = sums.get(bucketKey);
    const countMap = counts.get(bucketKey);
    for (const key of keys) {
      const sum = sumMap?.get(key);
      const count = countMap?.get(key);
      point[key] = sum != null && count ? sum / count : null;
    }
    return point;
  });
}
