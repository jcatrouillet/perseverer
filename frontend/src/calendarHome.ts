// Where "Calendar" links go. Not "/": that is the athlete's starting page, which can be the
// activity list or the last imported activity, and the Calendar tab must always open a calendar.
// It follows the starting page when that is a calendar view (day or month), else this week.
import type { DefaultView } from "./api/types";
import { localIsoDate } from "./dateUtils";

export function calendarHomeHref(defaultView: DefaultView, today: Date = new Date()): string {
  if (defaultView === "month") {
    const month = String(today.getMonth() + 1).padStart(2, "0");
    return `/calendar/${today.getFullYear()}/${month}`;
  }
  if (defaultView === "day") return `/day/${localIsoDate(today)}`;
  return `/calendar/week/${localIsoDate(today)}`;
}

/** Whether "/" itself shows a calendar view for this starting page. */
export function startingPageIsCalendar(defaultView: DefaultView): boolean {
  return defaultView === "week" || defaultView === "month" || defaultView === "day";
}
