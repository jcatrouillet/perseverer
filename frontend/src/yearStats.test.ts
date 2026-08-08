import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "./api/types";
import {
  activityTypeCounts,
  averageDistanceM,
  averageDurationS,
  averageHeartRateBpm,
  averageSpeedKmh,
  busiestMonth,
  busiestWeekStart,
  busiestYear,
  longestActivity,
  maxHeartRateBpm,
  totalElevationM,
} from "./yearStats";

function activity(
  local_date: string,
  distance_m: number | null,
  overrides: Partial<ActivitySummary> = {},
): ActivitySummary {
  return {
    id: local_date,
    start_time_utc: `${local_date}T12:00:00Z`,
    utc_offset_s: 0,
    local_date,
    sport: "running",
    sub_sport: null,
    name: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m,
    elevation_gain_m: null,
    calories: null,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    primary_source: "test",
    stream_available: false,
    ...overrides,
  };
}

describe("totalElevationM", () => {
  it("sums elevation gain, treating missing values as 0", () => {
    const total = totalElevationM([
      activity("2025-01-01", 1000, { elevation_gain_m: 50 }),
      activity("2025-01-02", 1000, { elevation_gain_m: null }),
      activity("2025-01-03", 1000, { elevation_gain_m: 30 }),
    ]);
    expect(total).toBe(80);
  });
});

describe("longestActivity", () => {
  it("picks the activity with the largest distance regardless of sport", () => {
    const result = longestActivity([
      activity("2025-01-01", 5000, { sport: "running" }),
      activity("2025-01-02", 42000, { sport: "cycling" }),
      activity("2025-01-03", 10000, { sport: "running" }),
    ]);
    expect(result).toEqual({ distanceM: 42000, date: "2025-01-02", sport: "cycling" });
  });

  it("returns null when no activity has a distance", () => {
    expect(longestActivity([activity("2025-01-01", null)])).toBeNull();
  });
});

describe("busiestMonth", () => {
  it("returns the month with the most activities", () => {
    expect(
      busiestMonth([
        activity("2025-01-01", 1000),
        activity("2025-02-01", 1000),
        activity("2025-02-15", 1000),
      ]),
    ).toBe(2);
  });

  it("returns null when there are no dated activities", () => {
    expect(busiestMonth([])).toBeNull();
  });
});

describe("busiestWeekStart", () => {
  it("returns the Monday of the week with the most activities", () => {
    // 2025-06-02 is a Monday; 2025-06-09 is the following Monday.
    expect(
      busiestWeekStart([
        activity("2025-06-02", 1000),
        activity("2025-06-09", 1000),
        activity("2025-06-10", 1000),
      ]),
    ).toBe("2025-06-09");
  });

  it("returns null when there are no dated activities", () => {
    expect(busiestWeekStart([])).toBeNull();
  });
});

describe("busiestYear", () => {
  it("returns the calendar year with the most activities", () => {
    expect(
      busiestYear([
        activity("2023-01-01", 1000),
        activity("2024-01-01", 1000),
        activity("2024-02-01", 1000),
      ]),
    ).toBe(2024);
  });

  it("returns null when there are no dated activities", () => {
    expect(busiestYear([])).toBeNull();
  });
});

describe("averageDistanceM", () => {
  it("only counts activities that actually have a distance", () => {
    const avg = averageDistanceM([
      activity("2025-01-01", 1000),
      activity("2025-01-02", null),
      activity("2025-01-03", 3000),
    ]);
    expect(avg).toBe(2000);
  });
});

describe("averageDurationS", () => {
  it("prefers moving_duration_s over duration_s", () => {
    const avg = averageDurationS([
      activity("2025-01-01", 1000, { duration_s: 2000, moving_duration_s: 1000 }),
      activity("2025-01-02", 1000, { duration_s: 2000, moving_duration_s: 1000 }),
    ]);
    expect(avg).toBe(1000);
  });
});

describe("averageSpeedKmh", () => {
  it("weights by total distance/time, not a naive average of per-activity speeds", () => {
    // 1km in 10min (6km/h) and 3km in 10min (18km/h) -- naive average would be 12km/h,
    // but the weighted total is 4km in 20min = 12km/h too here; use an asymmetric case instead.
    const avg = averageSpeedKmh([
      activity("2025-01-01", 1000, { moving_duration_s: 600 }), // 1km in 10min
      activity("2025-01-02", 9000, { moving_duration_s: 1800 }), // 9km in 30min
    ]);
    // Weighted: 10km total / (40min/60) h = 15 km/h. Naive average-of-speeds would be 12 km/h.
    expect(avg).toBeCloseTo(15, 5);
  });
});

describe("averageHeartRateBpm / maxHeartRateBpm", () => {
  it("averages avg_hr_bpm across activities that have it", () => {
    const avg = averageHeartRateBpm([
      activity("2025-01-01", 1000, { avg_hr_bpm: 120 }),
      activity("2025-01-02", 1000, { avg_hr_bpm: null }),
      activity("2025-01-03", 1000, { avg_hr_bpm: 140 }),
    ]);
    expect(avg).toBe(130);
  });

  it("finds the peak of max_hr_bpm across all activities", () => {
    const max = maxHeartRateBpm([
      activity("2025-01-01", 1000, { max_hr_bpm: 160 }),
      activity("2025-01-02", 1000, { max_hr_bpm: 175 }),
    ]);
    expect(max).toBe(175);
  });
});

describe("activityTypeCounts", () => {
  it("counts per sport, descending, ties broken alphabetically", () => {
    const counts = activityTypeCounts([
      activity("2025-01-01", 1000, { sport: "running" }),
      activity("2025-01-02", 1000, { sport: "cycling" }),
      activity("2025-01-03", 1000, { sport: "running" }),
      activity("2025-01-04", 1000, { sport: "training" }),
    ]);
    expect(counts).toEqual([
      { sport: "running", count: 2 },
      { sport: "cycling", count: 1 },
      { sport: "training", count: 1 },
    ]);
  });

  it("shows training activities under their sub_sport, matching how Garmin Connect displays them", () => {
    const counts = activityTypeCounts([
      activity("2025-01-01", 1000, { sport: "training", sub_sport: "yoga" }),
      activity("2025-01-02", 1000, { sport: "training", sub_sport: "yoga" }),
      activity("2025-01-03", 1000, { sport: "training", sub_sport: "strength_training" }),
    ]);
    expect(counts).toEqual([
      { sport: "yoga", count: 2 },
      { sport: "strength_training", count: 1 },
    ]);
  });
});
