import { describe, expect, it } from "vitest";

import type { DayRollupOut } from "./api/types";
import {
  eachDate,
  isoWeekNumber,
  mondayOf,
  monthGridWeeks,
  monthRange,
  parseIsoDate,
  startOfWeek,
  sumDayRollups,
  weekdayLabels,
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

describe("startOfWeek", () => {
  it("defaults to Monday-start", () => {
    expect(startOfWeek(parseIsoDate("2025-06-04")).toISOString().slice(0, 10)).toBe("2025-06-02");
  });

  it("returns the same date when already the requested start day", () => {
    expect(startOfWeek(parseIsoDate("2025-06-08"), "sunday").toISOString().slice(0, 10)).toBe(
      "2025-06-08",
    );
  });

  it("returns that week's Sunday for a mid-week date when weekStartDay is sunday", () => {
    expect(startOfWeek(parseIsoDate("2025-06-04"), "sunday").toISOString().slice(0, 10)).toBe(
      "2025-06-01",
    );
  });
});

describe("weekdayLabels", () => {
  it("defaults to Monday-first", () => {
    expect(weekdayLabels()).toEqual(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);
  });

  it("rotates to Sunday-first", () => {
    expect(weekdayLabels("sunday")).toEqual(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]);
  });
});

describe("weekRange", () => {
  it("returns Monday-Sunday for any date in the week", () => {
    expect(weekRange("2025-06-04")).toEqual({ start: "2025-06-02", end: "2025-06-08" });
  });

  it("returns Sunday-Saturday for any date in the week when weekStartDay is sunday", () => {
    expect(weekRange("2025-06-04", "sunday")).toEqual({ start: "2025-06-01", end: "2025-06-07" });
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

  it("aligns rows to Sunday instead when weekStartDay is sunday", () => {
    // June 2025: Jun 1 is itself a Sunday, so the first row starts exactly there.
    const weeks = monthGridWeeks(2025, 6, "sunday");
    for (const week of weeks) {
      expect(week).toHaveLength(7);
      expect(new Date(week[0]! + "T00:00:00Z").getUTCDay()).toBe(0);
    }
    expect(weeks[0]![0]).toBe("2025-06-01");
  });
});

describe("sumDayRollups", () => {
  function day(overrides: Partial<DayRollupOut> = {}): DayRollupOut {
    return {
      local_date: "2026-01-01",
      activity_count: 0,
      activity_duration_s: null,
      activity_moving_duration_s: null,
      activity_distance_m: null,
      activity_elevation_gain_m: null,
      activity_calories: null,
      sleep_total_s: null,
      sleep_score: null,
      health_metrics: [],
      ...overrides,
    };
  }

  it("sums counts/distance/duration across days, treating a null field as 0", () => {
    const result = sumDayRollups([
      day({ activity_count: 1, activity_distance_m: 5000, activity_moving_duration_s: 1500 }),
      day({ activity_count: 2, activity_distance_m: 3000, activity_moving_duration_s: null }),
      day({ activity_count: 0 }),
    ]);
    expect(result.activity_count).toBe(3);
    expect(result.activity_distance_m).toBe(8000);
    expect(result.activity_moving_duration_s).toBe(1500);
    expect(result.activity_days_count).toBe(2);
  });

  it("returns null elevation when no day recorded any elevation channel", () => {
    const result = sumDayRollups([
      day({ activity_count: 1, activity_elevation_gain_m: null }),
      day({ activity_count: 1, activity_elevation_gain_m: null }),
    ]);
    expect(result.activity_elevation_gain_m).toBeNull();
  });

  it("sums real elevation values even when mixed with days that recorded none", () => {
    const result = sumDayRollups([
      day({ activity_count: 1, activity_elevation_gain_m: 40 }),
      day({ activity_count: 1, activity_elevation_gain_m: null }),
    ]);
    expect(result.activity_elevation_gain_m).toBe(40);
  });

  it("returns all zeros (not null elevation-excepted) for an empty list", () => {
    const result = sumDayRollups([]);
    expect(result.activity_count).toBe(0);
    expect(result.activity_distance_m).toBe(0);
    expect(result.activity_elevation_gain_m).toBeNull();
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
