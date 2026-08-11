import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "./api/types";
import {
  amPmCounts,
  DAILY_HEATMAP_SCALE,
  dailyStats,
  distanceByDay,
  distanceByYear,
  distinctActiveDates,
  formatDurationHM,
  formatMinPerKm,
  formatPaceMinPerKm,
  isLongRun,
  isPaceSport,
  isPlausibleRunPace,
  localHour,
  localTimeLabel,
  longestStreakAndBreak,
  longRunPieDeg,
  metMinutes,
  monthlyDistanceM,
  nearestLegendKm,
  personalRecords,
  rollingDistanceKm,
  scatterPointOpacities,
  shortRunHeatPct,
  streamSpeedValue,
  weekdayIndex,
  weekdayStats,
  weeklyDistanceSeries,
  WEEKLY_HEATMAP_SCALE,
} from "./runningStats";

function activity(
  local_date: string,
  distance_m: number,
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
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    primary_source: "test",
    stream_available: false,
    ...overrides,
  };
}

describe("monthlyDistanceM", () => {
  it("buckets distance by calendar month", () => {
    const totals = monthlyDistanceM([
      activity("2025-01-05", 1000),
      activity("2025-01-20", 2000),
      activity("2025-03-01", 500),
    ]);
    expect(totals[0]).toBe(3000);
    expect(totals[2]).toBe(500);
    expect(totals[1]).toBe(0);
  });
});

describe("formatPaceMinPerKm", () => {
  it("computes pace from duration and distance", () => {
    // 30:00 for 5km -> 6:00/km.
    expect(formatPaceMinPerKm(1800, 5000)).toBe("6:00");
  });

  it("returns an em dash rather than dividing by zero when distance is zero", () => {
    expect(formatPaceMinPerKm(1800, 0)).toBe("—");
  });
});

describe("formatDurationHM", () => {
  it("formats under an hour as minutes only", () => {
    expect(formatDurationHM(42 * 60)).toBe("42m");
  });

  it("formats an hour or more as \"Xh Ym\"", () => {
    expect(formatDurationHM(74 * 60)).toBe("1h 14m");
  });
});

describe("isPaceSport", () => {
  it("treats foot sports as pace sports", () => {
    expect(isPaceSport("running")).toBe(true);
    expect(isPaceSport("walking")).toBe(true);
    expect(isPaceSport("hiking")).toBe(true);
  });

  it("treats wheeled/oared sports as speed sports, not pace", () => {
    expect(isPaceSport("cycling")).toBe(false);
    expect(isPaceSport("rowing")).toBe(false);
  });
});

describe("streamSpeedValue", () => {
  it("converts a foot sport's speed to pace (min/km)", () => {
    // 1000m at 10 min/km -> 600s -> 1.667 m/s.
    expect(streamSpeedValue("running", 1000 / 600)).toBeCloseTo(10, 5);
  });

  it("converts a wheeled sport's speed to km/h", () => {
    expect(streamSpeedValue("cycling", 10)).toBeCloseTo(36, 5);
  });

  it("treats near-zero speed as stationary (null pace) for a foot sport", () => {
    // Below the 0.3 m/s floor -- a real pause, not a legitimate multi-thousand min/km pace spike.
    expect(streamSpeedValue("running", 0.1)).toBeNull();
  });

  it("does not apply the pace floor to a wheeled sport (0 km/h is a valid reading)", () => {
    expect(streamSpeedValue("cycling", 0)).toBe(0);
  });

  it("passes through null unchanged", () => {
    expect(streamSpeedValue("running", null)).toBeNull();
  });
});

describe("formatMinPerKm", () => {
  it("formats a plain pace as M:SS", () => {
    expect(formatMinPerKm(5.5)).toBe("5:30");
  });

  it("carries a rounded-up 60 seconds into the next minute (a confirmed real bug)", () => {
    // 6.9992 minutes: floor is 6, (0.9992 * 60) rounds to 60 -- must not print "6:60".
    expect(formatMinPerKm(6.9992)).toBe("7:00");
  });
});

describe("distanceByDay", () => {
  it("produces one bucket per calendar day in range, zero-filled where there's no run", () => {
    const buckets = distanceByDay(
      [activity("2025-06-02", 5000), activity("2025-06-04", 3000)],
      "2025-06-01",
      "2025-06-05",
    );
    expect(buckets).toEqual([
      { label: "1", km: 0 },
      { label: "2", km: 5 },
      { label: "3", km: 0 },
      { label: "4", km: 3 },
      { label: "5", km: 0 },
    ]);
  });
});

describe("distanceByYear", () => {
  it("sums each calendar year into its own bucket, not collapsed by month across years", () => {
    const buckets = distanceByYear(
      [
        activity("2022-01-15", 5000),
        activity("2023-01-15", 3000),
        activity("2023-06-01", 2000),
      ],
      "2022-01-01",
      "2023-12-31",
    );
    expect(buckets).toEqual([
      { label: "2022", km: 5 },
      { label: "2023", km: 5 },
    ]);
  });
});

describe("scatterPointOpacities", () => {
  it("gives a repeated distance/pace combo full opacity and a one-off point the fade floor", () => {
    const points = scatterPointOpacities([
      { km: 5, pace: 5 },
      { km: 5, pace: 5.1 }, // same coarse bucket as the point above (nearest 1km, 0.5 min/km)
      { km: 12, pace: 6 }, // no other point shares its bucket
    ]);
    expect(points[0]!.opacity).toBe(1);
    expect(points[1]!.opacity).toBe(1);
    expect(points[2]!.opacity).toBe(0.2);
  });

  it("gives every point full opacity when nothing repeats and there's only one point", () => {
    const points = scatterPointOpacities([{ km: 10, pace: 5 }]);
    expect(points[0]!.opacity).toBe(1);
  });
});

describe("heatmap scale", () => {
  it("the weekly scale's full circle is 70km, not the daily scale's marathon", () => {
    expect(WEEKLY_HEATMAP_SCALE.fullCircleKm).toBe(70);
    expect(DAILY_HEATMAP_SCALE.fullCircleKm).toBe(42.195);
  });

  it("switches from gradient to pie exactly at the scale's own boundary", () => {
    expect(isLongRun(40, WEEKLY_HEATMAP_SCALE)).toBe(false);
    expect(isLongRun(40.1, WEEKLY_HEATMAP_SCALE)).toBe(true);
    expect(isLongRun(10, DAILY_HEATMAP_SCALE)).toBe(false);
    expect(isLongRun(10.1, DAILY_HEATMAP_SCALE)).toBe(true);
  });

  it("fills the weekly pie completely at 70km and clamps beyond it", () => {
    expect(longRunPieDeg(70, WEEKLY_HEATMAP_SCALE)).toBe(360);
    expect(longRunPieDeg(72.2, WEEKLY_HEATMAP_SCALE)).toBe(360); // the biggest real week on record
  });

  it("scales the weekly pie linearly between its own boundary and 70km", () => {
    // Halfway from 40km to 70km is 55km -> a half-filled circle.
    expect(longRunPieDeg(55, WEEKLY_HEATMAP_SCALE)).toBeCloseTo(180, 5);
  });

  it("buckets a real week's distance to the nearest weekly legend reference", () => {
    expect(nearestLegendKm(52, WEEKLY_HEATMAP_SCALE)).toBe(50);
    expect(nearestLegendKm(38, WEEKLY_HEATMAP_SCALE)).toBe(40);
  });

  it("keeps the daily and weekly gradient floors independent", () => {
    // Same 8km distance: unremarkable for a week (scaled against a 40km ceiling), but most of
    // the way up the scale for a single day (scaled against a 10km ceiling).
    const daily = shortRunHeatPct(8, DAILY_HEATMAP_SCALE);
    const weekly = shortRunHeatPct(8, WEEKLY_HEATMAP_SCALE);
    expect(daily).toBeGreaterThan(weekly);
  });
});

describe("weekdayStats", () => {
  it("uses Monday=0..Sunday=6, matching the app's Monday-start convention", () => {
    // 2025-06-02 is a Monday (confirmed elsewhere in dateUtils.test.ts).
    const stats = weekdayStats([activity("2025-06-02", 5000)]);
    expect(stats[0]!.day).toBe(0);
    expect(stats[0]!.count).toBe(1);
    expect(stats[0]!.avgDistanceM).toBe(5000);
    expect(stats[6]!.count).toBe(0);
  });

  it("averages distance across multiple activities on the same weekday", () => {
    const stats = weekdayStats([activity("2025-06-02", 4000), activity("2025-06-09", 6000)]);
    expect(stats[0]!.count).toBe(2);
    expect(stats[0]!.avgDistanceM).toBe(5000);
  });
});

describe("distinctActiveDates", () => {
  it("dedupes multiple activities on the same day and sorts ascending", () => {
    expect(
      distinctActiveDates([
        activity("2025-06-03", 1000),
        activity("2025-06-01", 1000),
        activity("2025-06-03", 2000),
      ]),
    ).toEqual(["2025-06-01", "2025-06-03"]);
  });
});

describe("longestStreakAndBreak", () => {
  it("finds a run of consecutive days as the longest streak", () => {
    const result = longestStreakAndBreak([
      "2025-06-01",
      "2025-06-02",
      "2025-06-03",
      "2025-06-10",
    ]);
    expect(result.longestStreakDays).toBe(3);
    expect(result.longestStreakStart).toBe("2025-06-01");
    expect(result.longestStreakEnd).toBe("2025-06-03");
  });

  it("resets the streak start when a later, longer streak beats an earlier one", () => {
    const result = longestStreakAndBreak([
      "2025-06-01",
      "2025-06-05",
      "2025-06-06",
      "2025-06-07",
      "2025-06-08",
    ]);
    expect(result.longestStreakDays).toBe(4);
    expect(result.longestStreakStart).toBe("2025-06-05");
    expect(result.longestStreakEnd).toBe("2025-06-08");
  });

  it("finds the largest gap between active dates as the longest break", () => {
    const result = longestStreakAndBreak(["2025-06-01", "2025-06-02", "2025-06-20"]);
    expect(result.longestBreakDays).toBe(17);
    expect(result.longestBreakStart).toBe("2025-06-02");
    expect(result.longestBreakEnd).toBe("2025-06-20");
  });

  it("handles a single date with no streak or break", () => {
    const result = longestStreakAndBreak(["2025-06-01"]);
    expect(result.longestStreakDays).toBe(1);
    expect(result.longestBreakDays).toBe(0);
  });

  it("handles no dates at all", () => {
    const result = longestStreakAndBreak([]);
    expect(result.longestStreakDays).toBe(0);
    expect(result.longestBreakDays).toBe(0);
  });
});

describe("rollingDistanceKm", () => {
  it("sums distance within the trailing window, dropping days that fall out of it", () => {
    const activities = [activity("2025-01-01", 1000), activity("2025-01-05", 2000)];
    const points = rollingDistanceKm(activities, "2025-01-01", "2025-01-10", 3);
    const byDate = new Map(points.map((p) => [p.local_date, p.distanceM]));
    expect(byDate.get("2025-01-01")).toBe(1000);
    expect(byDate.get("2025-01-03")).toBe(1000); // still within a 3-day trailing window
    expect(byDate.get("2025-01-04")).toBe(0); // Jan 1 has fallen out of the window
    expect(byDate.get("2025-01-05")).toBe(2000);
    expect(byDate.get("2025-01-07")).toBe(2000);
    expect(byDate.get("2025-01-08")).toBe(0);
  });

  it("produces one point per day in the requested range", () => {
    const points = rollingDistanceKm([], "2025-01-01", "2025-01-05", 90);
    expect(points).toHaveLength(5);
  });
});

describe("dailyStats", () => {
  it("sums distance, duration, and elevation across same-day activities", () => {
    const stats = dailyStats([
      activity("2025-06-01", 5000, { duration_s: 1500, elevation_gain_m: 50 }),
      activity("2025-06-01", 3000, { duration_s: 900, elevation_gain_m: 20 }),
    ]);
    expect(stats.get("2025-06-01")).toEqual({
      distanceM: 8000,
      durationS: 2400,
      elevationGainM: 70,
    });
  });

  it("treats a missing elevation_gain_m as 0 rather than dropping the day", () => {
    const stats = dailyStats([activity("2025-06-01", 5000, { elevation_gain_m: null })]);
    expect(stats.get("2025-06-01")?.elevationGainM).toBe(0);
  });
});

describe("localHour", () => {
  it("derives local hour from the activity's own utc_offset_s, not UTC", () => {
    // 08:00 UTC with a -7h offset (Mountain Time) is 01:00 local.
    const a = activity("2025-06-01", 5000, {
      start_time_utc: "2025-06-01T08:00:00Z",
      utc_offset_s: -25200,
    });
    expect(localHour(a)).toBe(1);
  });

  it("handles a positive offset crossing into the next UTC day", () => {
    // 22:00 UTC with a +3h offset is 01:00 the next local day.
    const a = activity("2025-06-01", 5000, {
      start_time_utc: "2025-06-01T22:00:00Z",
      utc_offset_s: 10800,
    });
    expect(localHour(a)).toBe(1);
  });
});

describe("localTimeLabel", () => {
  it("formats a morning local time with leading-zero minutes", () => {
    // 13:05 UTC with a -7h offset is 06:05 local.
    const a = activity("2025-06-01", 5000, {
      start_time_utc: "2025-06-01T13:05:00Z",
      utc_offset_s: -25200,
    });
    expect(localTimeLabel(a)).toBe("6:05 AM");
  });

  it("formats noon as 12 PM, not 0 PM", () => {
    const a = activity("2025-06-01", 5000, {
      start_time_utc: "2025-06-01T12:00:00Z",
      utc_offset_s: 0,
    });
    expect(localTimeLabel(a)).toBe("12:00 PM");
  });

  it("formats midnight as 12 AM, not 0 AM", () => {
    const a = activity("2025-06-01", 5000, {
      start_time_utc: "2025-06-01T00:00:00Z",
      utc_offset_s: 0,
    });
    expect(localTimeLabel(a)).toBe("12:00 AM");
  });
});

describe("amPmCounts", () => {
  it("splits activities by local hour, not UTC hour", () => {
    const activities = [
      // 08:00 UTC, -7h offset -> 01:00 local -> AM.
      activity("2025-06-01", 5000, { start_time_utc: "2025-06-01T08:00:00Z", utc_offset_s: -25200 }),
      // 20:00 UTC, -7h offset -> 13:00 local -> PM.
      activity("2025-06-02", 5000, { start_time_utc: "2025-06-02T20:00:00Z", utc_offset_s: -25200 }),
    ];
    expect(amPmCounts(activities)).toEqual({ am: 1, pm: 1 });
  });
});

describe("personalRecords", () => {
  it("picks the fastest activity within a tolerance band of each standard distance", () => {
    const activities = [
      // 5.1km in 25:00 moving -> 4:54/km.
      activity("2025-05-01", 5100, { duration_s: 1600, moving_duration_s: 1500 }),
      // 5.05km in 23:20 moving -> faster.
      activity("2025-05-15", 5050, { duration_s: 1500, moving_duration_s: 1400 }),
      // marathon, not a 5k match.
      activity("2025-06-01", 42195, { duration_s: 14500, moving_duration_s: 14400 }),
    ];
    const records = personalRecords(activities);
    const fiveK = records.find((r) => r.label === "5 km");
    expect(fiveK).toBeDefined();
    expect(fiveK!.date).toBe("2025-05-15");
    expect(fiveK!.eligibleCount).toBe(2);
  });

  it("uses moving time over elapsed time when both are present (a confirmed real bug fix)", () => {
    // Same elapsed time, but activity B paused for a long break -- its true moving pace is
    // much faster and should win, even though its elapsed time is slower.
    const activities = [
      activity("2025-05-01", 5000, { duration_s: 1500, moving_duration_s: 1500 }), // 5:00/km
      activity("2025-05-15", 5000, { duration_s: 1500, moving_duration_s: 1200 }), // 4:00/km moving
    ];
    const records = personalRecords(activities);
    const fiveK = records.find((r) => r.label === "5 km")!;
    expect(fiveK.date).toBe("2025-05-15");
    expect(fiveK.paceMinPerKm).toBeCloseTo(4, 5);
  });

  it("omits a distance with no eligible activity rather than inventing one", () => {
    const records = personalRecords([activity("2025-05-01", 5000, { duration_s: 1500 })]);
    expect(records.find((r) => r.label === "Half marathon")).toBeUndefined();
  });

  it("computes pace and speed from the matched activity's own real numbers", () => {
    const records = personalRecords([
      activity("2025-05-01", 5000, { duration_s: 1600, moving_duration_s: 1500 }),
    ]);
    const fiveK = records.find((r) => r.label === "5 km")!;
    expect(fiveK.paceMinPerKm).toBeCloseTo(5, 5); // 25:00 moving for 5km = 5:00/km
    expect(fiveK.speedKmh).toBeCloseTo(12, 5);
  });
});

describe("isPlausibleRunPace", () => {
  it("accepts the real archive's slowest genuine run (7.6 min/km)", () => {
    // 5km in 38 minutes -> 7.6 min/km.
    expect(isPlausibleRunPace(38 * 60, 5000)).toBe(true);
  });

  it("rejects the real archive's mislabeled hikes (10.9-15.3 min/km), which sit well past the gap", () => {
    // A real one: 1.77km in 19.3 minutes -> 10.9 min/km.
    expect(isPlausibleRunPace(19.3 * 60, 1770)).toBe(false);
  });

  it("rejects a zero-or-negative distance rather than dividing by it", () => {
    expect(isPlausibleRunPace(1800, 0)).toBe(false);
  });
});

describe("metMinutes", () => {
  it("computes MET-minutes as (calories / weight_kg) * 60, the standard gross-MET approximation", () => {
    // A real archived run: 1978 kcal at 80.1 kg body weight.
    expect(metMinutes(1978, 80.1)).toBeCloseTo(1481.6, 1);
  });

  it("scales linearly with calories and inversely with weight", () => {
    expect(metMinutes(200, 100)).toBeCloseTo(120, 5); // 2 MET-hours = 120 MET-minutes
    expect(metMinutes(400, 100)).toBeCloseTo(240, 5);
    expect(metMinutes(200, 50)).toBeCloseTo(240, 5);
  });
});

describe("weekdayIndex", () => {
  it("maps Monday to 0 and Sunday to 6, not JS's native Sunday=0", () => {
    expect(weekdayIndex("2025-06-02")).toBe(0); // a real Monday
    expect(weekdayIndex("2025-06-08")).toBe(6); // the following Sunday
  });
});

describe("weeklyDistanceSeries", () => {
  it("buckets distance into one point per Monday-starting week across the range", () => {
    const activities = [
      activity("2025-06-02", 5000), // week of 2025-06-02 (Mon)
      activity("2025-06-04", 3000), // same week
      activity("2025-06-09", 10000), // next week
    ];
    const series = weeklyDistanceSeries(activities, "2025-06-02", "2025-06-15");
    expect(series).toEqual([
      { weekStart: "2025-06-02", km: 8 },
      { weekStart: "2025-06-09", km: 10 },
    ]);
  });

  it("includes weeks with zero distance as real zero-km points, not gaps", () => {
    const activities = [activity("2025-06-02", 5000)];
    const series = weeklyDistanceSeries(activities, "2025-06-02", "2025-06-15");
    expect(series.map((p) => p.km)).toEqual([5, 0]);
  });
});
