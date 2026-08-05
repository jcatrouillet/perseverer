import { describe, expect, it } from "vitest";

import { eachDate, mondayOf, monthRange, parseIsoDate, weekRange, yearRange } from "./dateUtils";

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
