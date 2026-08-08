// Date-grid helpers for the calendar views. Week starts Monday, mirroring the backend's own
// rollups.py::week_start_monday exactly (confirmed against the user's Garmin Connect account
// -- see docs/adr/0009-phase-6-calendar-rollups-fitness-health.md).

// A handful of activities carry dates like 1989-12-30 -- a well-known GPS week-number rollover
// clock bug on some devices (consumer GPS didn't exist before the mid-1990s, so any earlier
// date is definitely not real). Excluded wherever "all available data" is computed, so an
// all-time range or year list reflects genuine history instead of a device clock glitch.
export const EARLIEST_PLAUSIBLE_DATE = "1995-01-01";

export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function parseIsoDate(iso: string): Date {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

export function mondayOf(date: Date): Date {
  const d = new Date(date);
  const day = d.getUTCDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setUTCDate(d.getUTCDate() + diff);
  return d;
}

export function weekRange(anyDateInWeek: string): { start: string; end: string } {
  const start = mondayOf(parseIsoDate(anyDateInWeek));
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 6);
  return { start: isoDate(start), end: isoDate(end) };
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

/** A month's dates as Monday-aligned week rows, padded with the adjacent month's dates so
 * every row has exactly 7 entries -- shared by MonthView's grid and DateNavigator's mini grid. */
export function monthGridWeeks(year: number, month: number): string[][] {
  const { start, end } = monthRange(year, month);
  const gridStart = mondayOf(parseIsoDate(start));
  const gridEnd = mondayOf(parseIsoDate(end));
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
