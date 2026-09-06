import { describe, expect, it } from "vitest";

import {
  bucketSeriesToWindow,
  computeWindow,
  earliestDate,
  shiftAnchor,
  type DailyPoint,
} from "./trendWindow";

describe("computeWindow", () => {
  it("week: buckets by day over a Monday-Sunday window", () => {
    const w = computeWindow("week", "2026-08-27", "2020-01-01", "2026-09-05");
    expect(w.bucketBy).toBe("day");
    expect(w.start).toBe("2026-08-24"); // Monday
    expect(w.end).toBe("2026-08-30"); // Sunday
    expect(w.bucketKeys).toHaveLength(7);
    expect(w.bucketLabels[0]).toBe("Mon 24");
  });

  it("month: buckets by week over a calendar month", () => {
    const w = computeWindow("month", "2026-08-15", "2020-01-01", "2026-09-05");
    expect(w.bucketBy).toBe("week");
    expect(w.start).toBe("2026-08-01");
    expect(w.end).toBe("2026-08-31");
    expect(w.label).toBe("August 2026");
    // Weeks (Monday-starting) touching August 2026: Jul 27, Aug 3, 10, 17, 24, 31.
    expect(w.bucketKeys).toEqual([
      "2026-07-27", "2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31",
    ]);
  });

  it("year: buckets by month over a calendar year", () => {
    const w = computeWindow("year", "2026-05-01", "2020-01-01", "2026-09-05");
    expect(w.bucketBy).toBe("month");
    expect(w.start).toBe("2026-01-01");
    expect(w.end).toBe("2026-12-31");
    expect(w.label).toBe("2026");
    expect(w.bucketLabels).toEqual([
      "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]);
  });

  it("all: buckets by month over the entire available range, with month+year labels", () => {
    const w = computeWindow("all", "irrelevant", "2024-11-15", "2025-02-10");
    expect(w.bucketBy).toBe("month");
    expect(w.start).toBe("2024-11-15");
    expect(w.end).toBe("2025-02-10");
    expect(w.bucketKeys).toEqual(["2024-11", "2024-12", "2025-01", "2025-02"]);
    expect(w.bucketLabels).toEqual(["Nov 2024", "Dec 2024", "Jan 2025", "Feb 2025"]);
    expect(w.canGoPrevious).toBe(false);
    expect(w.canGoNext).toBe(false);
  });

  it("week: cannot go past the athlete's earliest data or into the future", () => {
    const atStart = computeWindow("week", "2026-01-01", "2026-01-01", "2026-09-05");
    expect(atStart.canGoPrevious).toBe(false);

    const atToday = computeWindow("week", "2026-09-05", "2020-01-01", "2026-09-05");
    expect(atToday.canGoNext).toBe(false);

    const midHistory = computeWindow("week", "2026-05-01", "2020-01-01", "2026-09-05");
    expect(midHistory.canGoPrevious).toBe(true);
    expect(midHistory.canGoNext).toBe(true);
  });
});

describe("shiftAnchor", () => {
  it("steps week resolution by 7 days", () => {
    const w = computeWindow("week", "2026-08-27", "2020-01-01", "2026-09-05");
    expect(shiftAnchor(w, 1)).toBe("2026-08-31");
    expect(shiftAnchor(w, -1)).toBe("2026-08-17");
  });

  it("steps month resolution by 1 calendar month, including across a year boundary", () => {
    const w = computeWindow("month", "2026-12-15", "2020-01-01", "2026-12-31");
    expect(shiftAnchor(w, 1)).toBe("2027-01-01");
    expect(shiftAnchor(w, -1)).toBe("2026-11-01");
  });

  it("steps year resolution by 1 year", () => {
    const w = computeWindow("year", "2026-05-01", "2020-01-01", "2026-09-05");
    expect(shiftAnchor(w, 1)).toBe("2027-01-01");
    expect(shiftAnchor(w, -1)).toBe("2025-01-01");
  });

  it("is a no-op for all-time", () => {
    const w = computeWindow("all", "x", "2020-01-01", "2026-09-05");
    expect(shiftAnchor(w, 1)).toBe(w.start);
  });
});

describe("earliestDate", () => {
  it("returns the minimum local_date across the given points", () => {
    expect(
      earliestDate([{ local_date: "2026-03-01" }, { local_date: "2020-06-15" }, { local_date: "2024-01-01" }]),
    ).toBe("2020-06-15");
  });

  it("falls back to EARLIEST_PLAUSIBLE_DATE when given no points", () => {
    expect(earliestDate([])).toBe("1995-01-01");
  });
});

describe("bucketSeriesToWindow", () => {
  it("week resolution passes each day's own value through unaveraged", () => {
    const points: DailyPoint[] = [
      { local_date: "2026-08-24", vo2max: 50 },
      { local_date: "2026-08-26", vo2max: 51 },
    ];
    const w = computeWindow("week", "2026-08-27", "2020-01-01", "2026-09-05");
    const result = bucketSeriesToWindow(points, ["vo2max"], w);
    expect(result).toHaveLength(7);
    expect(result[0]).toMatchObject({ x: "Mon 24", vo2max: 50 });
    expect(result[1]).toMatchObject({ x: "Tue 25", vo2max: null });
    expect(result[2]).toMatchObject({ x: "Wed 26", vo2max: 51 });
  });

  it("month resolution averages each week's own days", () => {
    const points: DailyPoint[] = [
      { local_date: "2026-08-03", weight_kg: 80 },
      { local_date: "2026-08-04", weight_kg: 82 },
      { local_date: "2026-08-10", weight_kg: 79 },
    ];
    const w = computeWindow("month", "2026-08-15", "2020-01-01", "2026-09-05");
    const result = bucketSeriesToWindow(points, ["weight_kg"], w);
    const augWeek1 = result.find((p) => p.x === "Aug 3");
    const augWeek2 = result.find((p) => p.x === "Aug 10");
    const augWeek0 = result.find((p) => p.x === "Jul 27");
    expect(augWeek1?.weight_kg).toBe(81); // (80 + 82) / 2
    expect(augWeek2?.weight_kg).toBe(79);
    expect(augWeek0?.weight_kg).toBeNull();
  });

  it("year resolution averages each month's own days", () => {
    const points: DailyPoint[] = [
      { local_date: "2026-03-01", hrv: 60 },
      { local_date: "2026-03-15", hrv: 64 },
    ];
    const w = computeWindow("year", "2026-01-01", "2020-01-01", "2026-09-05");
    const result = bucketSeriesToWindow(points, ["hrv"], w);
    const march = result.find((p) => p.x === "Mar");
    expect(march?.hrv).toBe(62);
  });

  it("ignores points outside the window's own start/end", () => {
    const points: DailyPoint[] = [
      { local_date: "2025-01-01", steps: 5000 },
      { local_date: "2026-08-25", steps: 9000 },
    ];
    const w = computeWindow("week", "2026-08-27", "2020-01-01", "2026-09-05");
    const result = bucketSeriesToWindow(points, ["steps"], w);
    const total = result.reduce((sum, p) => sum + (typeof p.steps === "number" ? p.steps : 0), 0);
    expect(total).toBe(9000); // the 2025 point must not leak into this week's window
  });

  it("keeps multiple series keys independent", () => {
    const points: DailyPoint[] = [{ local_date: "2026-08-24", systolic: 120, diastolic: 80 }];
    const w = computeWindow("week", "2026-08-27", "2020-01-01", "2026-09-05");
    const result = bucketSeriesToWindow(points, ["systolic", "diastolic"], w);
    expect(result[0]).toMatchObject({ systolic: 120, diastolic: 80 });
  });
});
