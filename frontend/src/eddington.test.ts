import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "./api/types";
import { computeEddingtonBars, computeEddingtonNumber, computeYearlyEddington } from "./eddington";

function run(local_date: string, distanceKm: number): ActivitySummary {
  return {
    id: `${local_date}-${distanceKm}`,
    start_time_utc: `${local_date}T10:00:00Z`,
    utc_offset_s: 0,
    local_date,
    sport: "running",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m: distanceKm * 1000,
    elevation_gain_m: null,
    max_altitude_m: null,
    calories: null,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    vdot: null,
  } as ActivitySummary;
}

describe("computeEddingtonNumber", () => {
  it("returns 0 for no runs", () => {
    expect(computeEddingtonNumber([])).toBe(0);
  });

  it("returns 0 when every run is under 1 km", () => {
    expect(computeEddingtonNumber([0.5, 0.8])).toBe(0);
  });

  it("matches a hand-worked example", () => {
    // Sorted desc: 10, 8, 8. E=1: 10>=1. E=2: 8>=2. E=3: 8>=3. Only 3 runs total, so E=3.
    expect(computeEddingtonNumber([10, 8, 8])).toBe(3);
  });

  it("is bounded by the total number of runs, however long each one is", () => {
    // Even a single 100km run can only ever reach E=1 -- there's no second run to back it up.
    expect(computeEddingtonNumber([100])).toBe(1);
  });

  it("is bounded by distance, however many runs exist", () => {
    // Ten 2km runs: E=1 (10>=1), E=2 (2>=2), E=3 needs a 3rd run >=3km -- none qualify.
    expect(computeEddingtonNumber(new Array(10).fill(2))).toBe(2);
  });

  it("is order-independent", () => {
    const distances = [3, 12, 1, 7, 5];
    const shuffled = [5, 1, 12, 3, 7];
    expect(computeEddingtonNumber(distances)).toBe(computeEddingtonNumber(shuffled));
  });
});

describe("computeYearlyEddington", () => {
  it("groups by calendar year, most recent first", () => {
    const activities = [
      run("2024-01-01", 5),
      run("2024-01-02", 5),
      run("2025-06-01", 10),
      run("2025-06-02", 10),
      run("2025-06-03", 10),
    ];
    const years = computeYearlyEddington(activities);
    expect(years.map((y) => y.year)).toEqual([2025, 2024]);
  });

  it("computes each year's own Eddington number independently", () => {
    const activities = [
      run("2024-01-01", 1),
      run("2025-06-01", 10),
      run("2025-06-02", 10),
      run("2025-06-03", 10),
    ];
    const years = computeYearlyEddington(activities);
    const y2024 = years.find((y) => y.year === 2024)!;
    const y2025 = years.find((y) => y.year === 2025)!;
    expect(y2024.eddingtonNumber).toBe(1);
    expect(y2025.eddingtonNumber).toBe(3);
  });

  it("computes progress toward the next Eddington number", () => {
    const activities = [run("2025-01-01", 10), run("2025-01-02", 8), run("2025-01-03", 8)];
    const years = computeYearlyEddington(activities);
    const y2025 = years[0]!;
    expect(y2025.eddingtonNumber).toBe(3);
    // Runs >= 4km: all three (10, 8, 8).
    expect(y2025.runsTowardNext).toBe(3);
    expect(y2025.runsNeededForNext).toBe(1);
  });

  it("ignores activities with no distance or no local_date", () => {
    const activities = [
      run("2025-01-01", 5),
      { ...run("2025-01-02", 5), distance_m: null },
      { ...run("2025-01-03", 5), local_date: null },
    ];
    const years = computeYearlyEddington(activities);
    expect(years[0]!.totalRuns).toBe(1);
  });

  it("returns an empty list when there are no runs at all", () => {
    expect(computeYearlyEddington([])).toEqual([]);
  });
});

describe("computeEddingtonBars", () => {
  it("returns [] for no runs", () => {
    expect(computeEddingtonBars([])).toEqual([]);
  });

  it("produces one bar per integer km up to the longest run, rounded up", () => {
    const bars = computeEddingtonBars([3.2]);
    expect(bars.map((b) => b.km)).toEqual([1, 2, 3, 4]);
  });

  it("counts runs at least as long as each threshold, and mirrors km on the diagonal", () => {
    // Sorted desc: 10, 8, 8 -- same fixture as the hand-worked Eddington example (E=3).
    const bars = computeEddingtonBars([10, 8, 8]);
    const byKm = new Map(bars.map((b) => [b.km, b]));
    expect(byKm.get(1)).toEqual({ km: 1, count: 3, diagonal: 1 });
    expect(byKm.get(3)).toEqual({ km: 3, count: 3, diagonal: 3 });
    expect(byKm.get(4)).toEqual({ km: 4, count: 3, diagonal: 4 });
    expect(byKm.get(8)).toEqual({ km: 8, count: 3, diagonal: 8 });
    expect(byKm.get(9)).toEqual({ km: 9, count: 1, diagonal: 9 });
    expect(byKm.get(10)).toEqual({ km: 10, count: 1, diagonal: 10 });
  });

  it("is a non-increasing step function", () => {
    const bars = computeEddingtonBars([12, 3, 7, 1, 9]);
    for (let i = 1; i < bars.length; i++) {
      expect(bars[i]!.count).toBeLessThanOrEqual(bars[i - 1]!.count);
    }
  });
});
