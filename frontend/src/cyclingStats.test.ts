import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "./api/types";
import { averageSpeedKmh, powerVsDistance, trailingDistance, weeklyBestFtp } from "./cyclingStats";

function ride(
  id: string,
  date: string,
  distanceM: number,
  seconds: number,
  avgPowerW: number | null = null,
): ActivitySummary {
  return {
    id,
    local_date: date,
    sport: "cycling",
    distance_m: distanceM,
    moving_duration_s: seconds,
    duration_s: seconds,
    avg_power_w: avgPowerW,
  } as ActivitySummary;
}

describe("trailingDistance", () => {
  it("reaches back before the period so the first days count earlier rides", () => {
    const history = [
      ride("old", "2025-12-20", 40000, 3600),
      ride("new", "2026-01-02", 20000, 3600),
    ];
    const at = (days: number) =>
      trailingDistance(history, "2026-01-01", "2026-01-03", days).find(
        (p) => p.local_date === "2026-01-02",
      )!.km;
    expect(at(7)).toBe(20); // the December ride is 13 days back
    expect(at(30)).toBe(60);
    expect(at(365)).toBe(60);
  });
});

describe("powerVsDistance", () => {
  it("keeps only rides with a power reading", () => {
    expect(
      powerVsDistance([ride("p", "2026-01-01", 30000, 3600, 180.4), ride("n", "2026-01-02", 1, 1)]),
    ).toEqual([{ id: "p", km: 30, watts: 180 }]);
  });
});

describe("averageSpeedKmh", () => {
  it("is total distance over total moving time, not an average of averages", () => {
    // 30 km in 1 h and 10 km in 1 h: 20 km/h, whereas averaging the two speeds also gives 20 --
    // so use unequal times: 30 km in 1 h + 10 km in 0.25 h = 40 km / 1.25 h = 32 km/h.
    expect(
      averageSpeedKmh([ride("a", "2026-01-01", 30000, 3600), ride("b", "2026-01-02", 10000, 900)]),
    ).toBeCloseTo(32);
    expect(averageSpeedKmh([])).toBeNull();
  });
});

describe("weeklyBestFtp", () => {
  it("is 95% of each week's best 20-minute power, null for a week with none", () => {
    const rides = [
      { ...ride("a", "2026-01-05", 20000, 3600), best_20min_power_w: 200 },
      { ...ride("b", "2026-01-07", 20000, 3600), best_20min_power_w: 220 },
    ] as ActivitySummary[];
    const points = weeklyBestFtp(rides, "2026-01-05", "2026-01-19");
    expect(points.map((p) => p.ftp)).toEqual([209, null, null]);
    expect(points[0]!.activityId).toBe("b");
  });
});
