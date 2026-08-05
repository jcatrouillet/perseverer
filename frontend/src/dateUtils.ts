// Date-grid helpers for the calendar views. Week starts Monday, mirroring the backend's own
// rollups.py::week_start_monday exactly (confirmed against the user's Garmin Connect account
// -- see docs/adr/0009-phase-6-calendar-rollups-fitness-health.md).

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
