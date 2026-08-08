import { describe, expect, it } from "vitest";

import {
  eachDate,
  isoWeekNumber,
  mondayOf,
  monthGridWeeks,
  monthRange,
  parseIsoDate,
  weekRange,
  yearRange,
} from "./dateUtils";

describe("mondayOf", () => {
  it("returns the same date when already a Monday", () => {
    expect(mondayOf(parseIsoDate("2025-06-02")).toISOString().slice(0, 10)).toBe("2025-06-02");
  });

  it("returns that week's Monday for a mid-week date", () => {
    expect(mondayOf(parseIsoDate("2025-06-04")).toISOString().slice(0, 10)).toBe("2025-06-02");
  });

  it("returns that week's Monday for a Sunday (not the next week's)", () => {
    expect(mondayOf(parseIsoDate("2025-06-08")).toISOString().slice(0, 10)).toBe("2025-06-02");
  });
});

describe("weekRange", () => {
  it("returns Monday-Sunday for any date in the week", () => {
    expect(weekRange("2025-06-04")).toEqual({ start: "2025-06-02", end: "2025-06-08" });
  });
});

describe("monthRange", () => {
  it("returns the first and last day of the month", () => {
    expect(monthRange(2025, 6)).toEqual({ start: "2025-06-01", end: "2025-06-30" });
  });

  it("handles February correctly (non-leap year)", () => {
    expect(monthRange(2025, 2)).toEqual({ start: "2025-02-01", end: "2025-02-28" });
  });

  it("handles February in a leap year", () => {
    expect(monthRange(2024, 2)).toEqual({ start: "2024-02-01", end: "2024-02-29" });
  });
});

describe("yearRange", () => {
  it("returns Jan 1 to Dec 31", () => {
    expect(yearRange(2025)).toEqual({ start: "2025-01-01", end: "2025-12-31" });
  });
});

describe("eachDate", () => {
  it("gap-fills every date in a range inclusive of both ends", () => {
    expect(eachDate("2025-06-01", "2025-06-03")).toEqual([
      "2025-06-01",
      "2025-06-02",
      "2025-06-03",
    ]);
  });

  it("returns a single date when start equals end", () => {
    expect(eachDate("2025-06-01", "2025-06-01")).toEqual(["2025-06-01"]);
  });

  it("crosses a month boundary correctly", () => {
    const dates = eachDate("2025-05-30", "2025-06-02");
    expect(dates).toEqual(["2025-05-30", "2025-05-31", "2025-06-01", "2025-06-02"]);
  });
});

describe("monthGridWeeks", () => {
  it("every row has exactly 7 dates, Monday first", () => {
    const weeks = monthGridWeeks(2025, 6);
    for (const week of weeks) {
      expect(week).toHaveLength(7);
      expect(new Date(week[0]! + "T00:00:00Z").getUTCDay()).toBe(1);
    }
  });

  it("pads with the adjacent month's dates so the first/last week are complete", () => {
    // June 2025: Jun 1 is a Sunday, so the first row starts May 26 (Monday).
    const weeks = monthGridWeeks(2025, 6);
    expect(weeks[0]![0]).toBe("2025-05-26");
    expect(weeks[0]![6]).toBe("2025-06-01");
  });
});

describe("isoWeekNumber", () => {
  it("returns 1 for the first ISO week of a year", () => {
    // Jan 1 2025 is a Wednesday; that week's Thursday (Jan 2) is in 2025, so it's week 1.
    expect(isoWeekNumber("2025-01-01")).toBe(1);
  });

  it("assigns a late-December date to next year's week 1 when its Thursday falls there", () => {
    // Dec 30 2024 is a Monday; that week's Thursday (Jan 2 2025) is in 2025.
    expect(isoWeekNumber("2024-12-30")).toBe(1);
  });

  it("matches a known mid-year week number", () => {
    // Counting Mondays from 2024-12-30 (week 1) by sevens lands on 2025-06-02 at week 23.
    expect(isoWeekNumber("2025-06-02")).toBe(23);
  });
});
