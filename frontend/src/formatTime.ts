// One shared implementation of the 12h-vs-24h clock-time branch, replacing what
// `runningStats.ts::localTimeLabel` and `BodyBatteryChart.tsx` each used to hand-roll
// independently (both always 12h, with no 24h option). Every clock-time display in the app
// should go through this, so a Personalize time-format change is instant everywhere rather than
// touching each call site's own formatting.
import { usePersonalize } from "./PersonalizeContext";
import type { TimeFormat } from "./api/types";

/** "06:32" (24h) or "6:32 AM" (12h) from an hour (0-23) and minute (0-59). */
export function formatClock(hour24: number, minute: number, format: TimeFormat): string {
  const mm = minute.toString().padStart(2, "0");
  if (format === "24h") {
    return `${hour24.toString().padStart(2, "0")}:${mm}`;
  }
  const period = hour24 < 12 ? "AM" : "PM";
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  return `${hour12}:${mm} ${period}`;
}

/** Formats a stored "HH:MM" 24h string (e.g. planned_workout.scheduled_time) for display. */
export function formatHHMM(hhmm: string, format: TimeFormat): string {
  const [h, m] = hhmm.split(":").map(Number);
  return formatClock(h ?? 0, m ?? 0, format);
}

/** Formats a Date's own browser-local wall-clock time (e.g. a UTC timestamp the browser already
 * converts to the viewer's local time) for display. */
export function formatTimeOfDay(date: Date, format: TimeFormat): string {
  return formatClock(date.getHours(), date.getMinutes(), format);
}

/** Convenience hook: the same three functions, pre-bound to the athlete's own current
 * time-format preference, so a call site just does `formatHHMM(w.scheduled_time)`. */
export function useTimeFormat() {
  const { time_format: format } = usePersonalize();
  return {
    format,
    formatClock: (hour24: number, minute: number) => formatClock(hour24, minute, format),
    formatHHMM: (hhmm: string) => formatHHMM(hhmm, format),
    formatTimeOfDay: (date: Date) => formatTimeOfDay(date, format),
  };
}
