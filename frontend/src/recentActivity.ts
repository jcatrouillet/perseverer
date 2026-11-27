// Which activity, if any, the "just finished" popup shows when the app is opened on a phone.
import type { ActivitySummary } from "./api/types";

/** How recent an activity's end must be for the popup to show it. */
export const RECENT_HOURS = 8;

/** Where the popup remembers, for this browser session, which activity it already showed. */
export const SHOWN_STORAGE_KEY = "perseverer.recentActivityShown";

function startMs(activity: ActivitySummary): number {
  const raw = activity.start_time_utc;
  // Storage is naive UTC; an offset-less string would otherwise be read as browser-local time.
  return Date.parse(/(?:Z|[+-]\d\d:?\d\d)$/.test(raw) ? raw : `${raw}Z`);
}

/** When the activity ended: its start plus its elapsed time (a pause counts, the clock kept
 * running), or the start itself when no duration is known. */
export function finishedAtMs(activity: ActivitySummary): number {
  return startMs(activity) + (activity.duration_s ?? activity.moving_duration_s ?? 0) * 1000;
}

/** The activity that finished last, if it finished within `hours` before `now`. An activity that
 * has not ended yet (a future end time, e.g. from clock skew) does not count. */
export function lastFinishedWithin(
  activities: readonly ActivitySummary[],
  now: Date,
  hours: number = RECENT_HOURS,
): ActivitySummary | null {
  const nowMs = now.getTime();
  let best: ActivitySummary | null = null;
  let bestEnd = -Infinity;
  for (const a of activities) {
    const end = finishedAtMs(a);
    if (Number.isNaN(end) || end > nowMs || nowMs - end > hours * 3_600_000) continue;
    if (end > bestEnd) {
      best = a;
      bestEnd = end;
    }
  }
  return best;
}
