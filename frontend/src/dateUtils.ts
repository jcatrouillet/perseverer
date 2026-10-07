// Date-grid helpers for the calendar views. Every function here defaults to Monday, mirroring
// the backend's own rollups.py::week_start_monday exactly (confirmed against the user's Garmin
// Connect account -- docs/ARCHITECTURE.md) -- that
// backend accounting week never changes. The optional `weekStartDay` param on weekRange/
// monthGridWeeks (read from PersonalizeContext by their callers) is purely a *frontend display*
// preference layered on top: Week/Month view and client-side weekly charts can start their own
// visible grid on Sunday instead, but every stored weekly total/rollup/note/report stays
// Monday-anchored regardless -- see AGENTS.md's own Personalize bullet for the full reasoning.

import type { DayRollupOut } from "./api/types";

// A handful of activities carry dates like 1989-12-30 -- a well-known GPS week-number rollover
// clock bug on some devices (consumer GPS didn't exist before the mid-1990s, so any earlier
// date is definitely not real). Excluded wherever "all available data" is computed, so an
// all-time range or year list reflects genuine history instead of a device clock glitch.
export const EARLIEST_PLAUSIBLE_DATE = "1995-01-01";

export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** The browser's own local calendar date as YYYY-MM-DD. `isoDate(new Date())` is the UTC date,
 * which already reads as tomorrow for hours every evening west of UTC (and as yesterday early
 * morning east of it) -- so anything meaning "today for this athlete" must use this instead. */
export function localIsoDate(date: Date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function parseIsoDate(iso: string): Date {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

export type WeekStartDay = "monday" | "sunday";

export function mondayOf(date: Date): Date {
  const d = new Date(date);
  const day = d.getUTCDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setUTCDate(d.getUTCDate() + diff);
  return d;
}

/** The start of the calendar-display week containing `date`, per `weekStartDay` -- Sunday-start
 * is `getUTCDay()` directly (already 0=Sun..6=Sat); Monday-start is `mondayOf`'s own math. */
export function startOfWeek(date: Date, weekStartDay: WeekStartDay = "monday"): Date {
  if (weekStartDay === "monday") return mondayOf(date);
  const d = new Date(date);
  d.setUTCDate(d.getUTCDate() - d.getUTCDay());
  return d;
}

export function weekRange(
  anyDateInWeek: string,
  weekStartDay: WeekStartDay = "monday",
): { start: string; end: string } {
  const start = startOfWeek(parseIsoDate(anyDateInWeek), weekStartDay);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 6);
  return { start: isoDate(start), end: isoDate(end) };
}

/** The 7 weekday labels in display order for `weekStartDay` -- replaces a hardcoded Mon-first
 * array wherever a calendar grid renders its own header row (MonthView, RunningStats heatmap). */
export function weekdayLabels(weekStartDay: WeekStartDay = "monday"): string[] {
  const monFirst = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return weekStartDay === "monday" ? monFirst : [monFirst[6]!, ...monFirst.slice(0, 6)];
}

export function monthRange(year: number, month: number): { start: string; end: string } {
  const start = new Date(Date.UTC(year, month - 1, 1));
  const end = new Date(Date.UTC(year, month, 0)); // day 0 of next month = last day of this one
  return { start: isoDate(start), end: isoDate(end) };
}

export function yearRange(year: number): { start: string; end: string } {
  return { start: `${year}-01-01`, end: `${year}-12-31` };
}

/** Every date from start to end inclusive, for client-side gap-filling a month grid. */
export function eachDate(start: string, end: string): string[] {
  const dates: string[] = [];
  const cursor = parseIsoDate(start);
  const last = parseIsoDate(end);
  while (cursor <= last) {
    dates.push(isoDate(cursor));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return dates;
}

/** A month's dates as week rows aligned to `weekStartDay`, padded with the adjacent month's
 * dates so every row has exactly 7 entries -- shared by MonthView's grid and DateNavigator's
 * mini grid. */
export function monthGridWeeks(
  year: number,
  month: number,
  weekStartDay: WeekStartDay = "monday",
): string[][] {
  const { start, end } = monthRange(year, month);
  const gridStart = startOfWeek(parseIsoDate(start), weekStartDay);
  const gridEnd = startOfWeek(parseIsoDate(end), weekStartDay);
  gridEnd.setUTCDate(gridEnd.getUTCDate() + 6);
  const gridDates = eachDate(isoDate(gridStart), isoDate(gridEnd));
  const weeks: string[][] = [];
  for (let i = 0; i < gridDates.length; i += 7) weeks.push(gridDates.slice(i, i + 7));
  return weeks;
}

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

export function monthName(month: number): string {
  return MONTH_NAMES[month - 1] ?? String(month);
}

/** ISO 8601 week number (1-53): the week containing this date's Thursday determines both the
 * week number and its ISO year, which can differ from the calendar year near Dec/Jan
 * boundaries -- see e.g. isoWeekNumber("2024-12-30") = 1 (belongs to 2025's week 1). Used only
 * as a compact "W<n>" label in DateNavigator, not for any date-range computation. */
export function isoWeekNumber(iso: string): number {
  const d = parseIsoDate(iso);
  const dayNum = d.getUTCDay() || 7; // Sunday=0 -> 7, so Monday=1..Sunday=7
  d.setUTCDate(d.getUTCDate() + 4 - dayNum); // move to this week's Thursday
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
}

export interface DayRollupSum {
  activity_count: number;
  activity_duration_s: number;
  activity_moving_duration_s: number;
  activity_distance_m: number;
  activity_elevation_gain_m: number | null;
  activity_days_count: number;
}

/** Sums the day-level fields a "week total"/"custom range total" stat card needs across a set
 * of `DayRollupOut` rows -- used by WeekView/MonthView instead of the Monday-keyed
 * `period_rollup` (`useCalendarWeeks`) so a week's own total stays correct for any
 * week-start-day display setting, not just Monday. Numerically identical to period_rollup's own
 * total for a Monday-aligned week, since it sums the same underlying daily data.
 * `activity_elevation_gain_m` stays `null` (hiding its own stat tile) when not one day in range
 * actually recorded any elevation channel, rather than silently reading as a real "0m" --
 * distinct from a real, summed flat-elevation range. */
export function sumDayRollups(days: DayRollupOut[]): DayRollupSum {
  const totals = days.reduce(
    (acc, d) => ({
      activity_count: acc.activity_count + d.activity_count,
      activity_duration_s: acc.activity_duration_s + (d.activity_duration_s ?? 0),
      activity_moving_duration_s:
        acc.activity_moving_duration_s + (d.activity_moving_duration_s ?? 0),
      activity_distance_m: acc.activity_distance_m + (d.activity_distance_m ?? 0),
      activity_elevation_gain_m: acc.activity_elevation_gain_m + (d.activity_elevation_gain_m ?? 0),
      activity_days_count: acc.activity_days_count + (d.activity_count > 0 ? 1 : 0),
      has_elevation: acc.has_elevation || d.activity_elevation_gain_m != null,
    }),
    {
      activity_count: 0,
      activity_duration_s: 0,
      activity_moving_duration_s: 0,
      activity_distance_m: 0,
      activity_elevation_gain_m: 0,
      activity_days_count: 0,
      has_elevation: false,
    },
  );
  return {
    ...totals,
    activity_elevation_gain_m: totals.has_elevation ? totals.activity_elevation_gain_m : null,
  };
}
