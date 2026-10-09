import { describe, expect, it } from "vitest";

import { calendarHomeHref, startingPageIsCalendar } from "./calendarHome";

const today = new Date(2026, 9, 8, 21, 30); // local time: Thu 8 Oct 2026, evening

describe("calendarHomeHref", () => {
  it("never links to the starting page itself", () => {
    expect(calendarHomeHref("last_activity", today)).toBe("/calendar/week/2026-10-08");
    expect(calendarHomeHref("activities", today)).toBe("/calendar/week/2026-10-08");
    expect(calendarHomeHref("week", today)).toBe("/calendar/week/2026-10-08");
  });

  it("follows a calendar starting page", () => {
    expect(calendarHomeHref("month", today)).toBe("/calendar/2026/10");
    expect(calendarHomeHref("day", today)).toBe("/day/2026-10-08");
  });
});

describe("startingPageIsCalendar", () => {
  it("is true only for the calendar views", () => {
    expect(startingPageIsCalendar("week")).toBe(true);
    expect(startingPageIsCalendar("day")).toBe(true);
    expect(startingPageIsCalendar("last_activity")).toBe(false);
    expect(startingPageIsCalendar("activities")).toBe(false);
  });
});
