import { describe, expect, it } from "vitest";

import type { HealthDashboardMetricOut, HealthObservationOut, SleepSessionOut } from "./api/types";
import {
  latestObservation,
  mergeTrendSeries,
  monthlyAverageSleepHours,
  valueForDate,
  weeklyAverageSleepHours,
  weightedAverage,
} from "./healthStats";

function observation(
  metric_key: string,
  observed_at_utc: string,
  overrides: Partial<HealthObservationOut> = {},
): HealthObservationOut {
  return {
    metric_key,
    observed_at_utc,
    local_date: observed_at_utc.slice(0, 10),
    aggregation: "instant",
    value_num: null,
    value_text: null,
    unit: null,
    source: "test",
    ...overrides,
  };
}

function metric(
  logical_metric: string,
  daily: { local_date: string; value_sum: number | null; n_observations: number }[],
): HealthDashboardMetricOut {
  return {
    logical_metric,
    last_observed: daily.length > 0 ? daily[daily.length - 1]!.local_date : null,
    daily: daily.map((d) => ({
      local_date: d.local_date,
      value_sum: d.value_sum,
      value_avg: d.value_sum != null ? d.value_sum / d.n_observations : null,
      value_min: null,
      value_max: null,
      value_last: d.value_sum != null ? d.value_sum / d.n_observations : null,
      n_observations: d.n_observations,
      source_metric_key: logical_metric,
    })),
  };
}

describe("weightedAverage", () => {
  it("weights by observation count, not a naive average of daily averages", () => {
    // Day 1: 1 observation averaging 10. Day 2: 3 observations averaging 20.
    // Naive average-of-averages would be 15; the weighted average should be 17.5.
    const m = metric("steps", [
      { local_date: "2025-01-01", value_sum: 10, n_observations: 1 },
      { local_date: "2025-01-02", value_sum: 60, n_observations: 3 },
    ]);
    expect(weightedAverage(m)).toBe(17.5);
  });

  it("returns null when there is no data at all", () => {
    expect(weightedAverage(metric("steps", []))).toBeNull();
  });

  it("returns null for an undefined metric", () => {
    expect(weightedAverage(undefined)).toBeNull();
  });

  it("skips days with zero observations", () => {
    const m = metric("steps", [
      { local_date: "2025-01-01", value_sum: 100, n_observations: 2 },
      { local_date: "2025-01-02", value_sum: 0, n_observations: 0 },
    ]);
    expect(weightedAverage(m)).toBe(50);
  });
});

describe("valueForDate", () => {
  it("returns the given day's value, not some other day's", () => {
    const m = metric("steps", [
      { local_date: "2025-01-01", value_sum: 8000, n_observations: 1 },
      { local_date: "2025-01-02", value_sum: 12000, n_observations: 1 },
    ]);
    expect(valueForDate(m, "2025-01-02")).toBe(12000);
  });

  it("returns null when the date has no data", () => {
    const m = metric("steps", [{ local_date: "2025-01-01", value_sum: 8000, n_observations: 1 }]);
    expect(valueForDate(m, "2025-01-02")).toBeNull();
  });

  it("returns null for an undefined metric", () => {
    expect(valueForDate(undefined, "2025-01-01")).toBeNull();
  });
});

describe("latestObservation", () => {
  it("picks the most recent of several same-day observations, not the first or last-inserted", () => {
    const obs = [
      observation("readiness.score", "2026-08-02T07:42:11Z", { value_num: 26 }),
      observation("readiness.score", "2026-08-02T14:44:06Z", { value_num: 35 }),
      observation("readiness.score", "2026-08-02T02:59:45Z", { value_num: 16 }),
    ];
    expect(latestObservation(obs, "readiness.score")?.value_num).toBe(35);
  });

  it("ignores observations for a different metric_key", () => {
    const obs = [
      observation("readiness.score", "2026-08-02T07:42:11Z", { value_num: 26 }),
      observation("training_status", "2026-08-02T14:44:06Z", { value_text: "PRODUCTIVE" }),
    ];
    expect(latestObservation(obs, "training_status")?.value_text).toBe("PRODUCTIVE");
  });

  it("returns null when the metric has no observation at all", () => {
    expect(latestObservation([], "readiness.score")).toBeNull();
  });
});

describe("mergeTrendSeries", () => {
  it("merges independent per-metric daily arrays into one row per date", () => {
    const hrv = metric("hrv_nightly_average", [
      { local_date: "2025-01-01", value_sum: 40, n_observations: 1 },
    ]);
    const spo2 = metric("spo2_average", [
      { local_date: "2025-01-01", value_sum: 95, n_observations: 1 },
      { local_date: "2025-01-02", value_sum: 96, n_observations: 1 },
    ]);
    const merged = mergeTrendSeries([hrv, spo2], ["hrv_nightly_average", "spo2_average"]);
    expect(merged).toHaveLength(2);
    expect(merged[0]).toMatchObject({
      local_date: "2025-01-01",
      hrv_nightly_average: 40,
      spo2_average: 95,
    });
    expect(merged[1]).toMatchObject({ local_date: "2025-01-02", spo2_average: 96 });
    expect(merged[1]!.hrv_nightly_average).toBeUndefined();
  });

  it("sorts merged points by date ascending", () => {
    const m = metric("stress_average", [
      { local_date: "2025-01-03", value_sum: 30, n_observations: 1 },
      { local_date: "2025-01-01", value_sum: 20, n_observations: 1 },
    ]);
    const merged = mergeTrendSeries([m], ["stress_average"]);
    expect(merged.map((p) => p.local_date)).toEqual(["2025-01-01", "2025-01-03"]);
  });

  it("skips logical metrics not present in the metrics array", () => {
    const merged = mergeTrendSeries([], ["hrv_nightly_average"]);
    expect(merged).toEqual([]);
  });
});

function sleepSession(local_date: string, total_sleep_s: number | null): SleepSessionOut {
  return {
    local_date,
    start_time_utc: `${local_date}T22:00:00Z`,
    end_time_utc: `${local_date}T06:00:00Z`,
    total_sleep_s,
    sleep_score: null,
    source: "test",
    stages: [],
  };
}

describe("weeklyAverageSleepHours", () => {
  it("averages sessions within the same Monday-starting ISO week", () => {
    // 2025-06-02 is a Monday; both nights fall in that same week.
    const points = weeklyAverageSleepHours(
      [sleepSession("2025-06-02", 7 * 3600), sleepSession("2025-06-04", 8 * 3600)],
      "2025-06-02",
      "2025-06-08",
    );
    expect(points).toEqual([{ weekStart: "2025-06-02", avgHours: 7.5 }]);
  });

  it("pads every week in range with null, not zero, when a week has no sessions", () => {
    const points = weeklyAverageSleepHours(
      [sleepSession("2025-06-02", 7 * 3600)],
      "2025-06-02",
      "2025-06-15",
    );
    expect(points).toEqual([
      { weekStart: "2025-06-02", avgHours: 7 },
      { weekStart: "2025-06-09", avgHours: null },
    ]);
  });

  it("ignores sessions with a null total_sleep_s", () => {
    const points = weeklyAverageSleepHours(
      [sleepSession("2025-06-02", null), sleepSession("2025-06-03", 6 * 3600)],
      "2025-06-02",
      "2025-06-08",
    );
    expect(points).toEqual([{ weekStart: "2025-06-02", avgHours: 6 }]);
  });
});

describe("monthlyAverageSleepHours", () => {
  it("averages sessions within the same calendar month", () => {
    const points = monthlyAverageSleepHours(
      [sleepSession("2025-06-01", 7 * 3600), sleepSession("2025-06-15", 9 * 3600)],
      "2025-06-01",
      "2025-06-30",
    );
    expect(points).toEqual([{ month: "2025-06", avgHours: 8 }]);
  });

  it("pads every month in range with null, not zero, when a month has no sessions", () => {
    const points = monthlyAverageSleepHours(
      [sleepSession("2025-06-01", 7 * 3600)],
      "2025-06-01",
      "2025-08-01",
    );
    expect(points).toEqual([
      { month: "2025-06", avgHours: 7 },
      { month: "2025-07", avgHours: null },
      { month: "2025-08", avgHours: null },
    ]);
  });

  it("buckets across a year boundary correctly", () => {
    const points = monthlyAverageSleepHours(
      [sleepSession("2024-12-15", 6 * 3600), sleepSession("2025-01-15", 8 * 3600)],
      "2024-12-01",
      "2025-01-31",
    );
    expect(points).toEqual([
      { month: "2024-12", avgHours: 6 },
      { month: "2025-01", avgHours: 8 },
    ]);
  });
});
