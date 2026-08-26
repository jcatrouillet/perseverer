import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HealthDashboardMetricOut } from "../api/types";
import { HealthMetricTiles } from "./HealthMetricTiles";

function metric(logical_metric: string, value: number): HealthDashboardMetricOut {
  return {
    logical_metric,
    last_observed: "2025-06-01",
    daily: [
      {
        local_date: "2025-06-01",
        value_sum: value,
        value_avg: value,
        value_min: value,
        value_max: value,
        value_last: value,
        n_observations: 1,
        source_metric_key: "test",
      },
    ],
  };
}

describe("HealthMetricTiles", () => {
  it("shows a tile for each key that has data", () => {
    render(<HealthMetricTiles metrics={[metric("steps", 8000)]} keys={["steps"]} />);
    expect(screen.getByText("steps")).toBeInTheDocument();
    expect(screen.getByText("8000.0")).toBeInTheDocument();
  });

  it("omits a tile for a key with no data instead of showing a dash", () => {
    render(
      <HealthMetricTiles metrics={[metric("steps", 8000)]} keys={["steps", "resting_heart_rate"]} />,
    );
    expect(screen.getByText("steps")).toBeInTheDocument();
    expect(screen.queryByText("resting heart rate")).not.toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("renders nothing when no key in the range has any data", () => {
    const { container } = render(<HealthMetricTiles metrics={[]} keys={["steps"]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
