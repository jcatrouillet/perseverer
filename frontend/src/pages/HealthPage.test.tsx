import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HealthDashboardMetricOut } from "../api/types";
import { MetricSection } from "./HealthPage";

function metric(
  logical_metric: string,
  last_observed: string | null,
  daily: HealthDashboardMetricOut["daily"] = [],
): HealthDashboardMetricOut {
  return { logical_metric, last_observed, daily };
}

describe("MetricSection", () => {
  it("only shows metrics whose key is in the requested list, in that order", () => {
    const metrics = [
      metric("steps", "2025-06-01"),
      metric("stress_average", "2025-06-01"),
      metric("resting_heart_rate", "2025-06-01"),
    ];
    render(<MetricSection title="Core" metrics={metrics} keys={["resting_heart_rate", "steps"]} />);
    // stress_average was seeded but not requested by this section -- must not appear.
    expect(screen.queryByText(/stress average/i)).not.toBeInTheDocument();
    expect(screen.getByText(/resting heart rate/i)).toBeInTheDocument();
    expect(screen.getByText(/^steps$/i)).toBeInTheDocument();
  });

  it("shows a placeholder when none of the requested keys have data", () => {
    render(<MetricSection title="HRV" metrics={[]} keys={["hrv_nightly_average"]} />);
    expect(screen.getByText(/no data for this section/i)).toBeInTheDocument();
  });

  it("shows the latest daily value and its date", () => {
    const metrics = [
      metric("steps", "2025-06-02", [
        {
          local_date: "2025-06-01",
          value_sum: 8000,
          value_avg: 8000,
          value_min: 8000,
          value_max: 8000,
          value_last: 8000,
          n_observations: 1,
          source_metric_key: "garmin.daily_summary.totalSteps",
        },
        {
          local_date: "2025-06-02",
          value_sum: 9500,
          value_avg: 9500,
          value_min: 9500,
          value_max: 9500,
          value_last: 9500,
          n_observations: 1,
          source_metric_key: "garmin.daily_summary.totalSteps",
        },
      ]),
    ];
    render(<MetricSection title="Core" metrics={metrics} keys={["steps"]} />);
    expect(screen.getByText(/9500/)).toBeInTheDocument();
    expect(screen.getByText(/2025-06-02/)).toBeInTheDocument();
  });

  it("surfaces a stale last_observed date distinct from the latest daily row", () => {
    const metrics = [metric("steps", "2025-01-15", [])];
    render(<MetricSection title="Core" metrics={metrics} keys={["steps"]} />);
    expect(screen.getByText(/no data in this range/i)).toBeInTheDocument();
    expect(screen.getByText(/last observed: 2025-01-15/i)).toBeInTheDocument();
  });
});
