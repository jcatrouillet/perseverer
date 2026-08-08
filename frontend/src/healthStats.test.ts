import { describe, expect, it } from "vitest";

import type { HealthDashboardMetricOut } from "./api/types";
import { mergeTrendSeries, weightedAverage } from "./healthStats";

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
