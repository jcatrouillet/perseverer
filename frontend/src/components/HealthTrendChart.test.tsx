import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HealthDashboardMetricOut } from "../api/types";
import { formatAxisTick, HealthTrendChart } from "./HealthTrendChart";

function metric(logical_metric: string, values: number[]): HealthDashboardMetricOut {
  return {
    logical_metric,
    last_observed: null,
    daily: values.map((v, i) => ({
      local_date: `2025-06-0${i + 1}`,
      value_sum: v,
      value_avg: v,
      value_min: v,
      value_max: v,
      value_last: v,
      n_observations: 1,
      source_metric_key: "test",
    })),
  };
}

describe("HealthTrendChart", () => {
  it("omits the legend when only one series has data -- the heading already names it", () => {
    const { container } = render(
      <HealthTrendChart
        metrics={[metric("resting_heart_rate", [48, 46, 49])]}
        keys={["resting_heart_rate"]}
      />,
    );
    expect(container.querySelector(".chart-legend")).not.toBeInTheDocument();
  });

  it("shows the legend once there are two or more series to tell apart", () => {
    const { container } = render(
      <HealthTrendChart
        metrics={[metric("sleep_respiration_rate", [14]), metric("waking_respiration_rate", [15])]}
        keys={["sleep_respiration_rate", "waking_respiration_rate"]}
      />,
    );
    const legend = container.querySelector(".chart-legend");
    expect(legend).toBeInTheDocument();
    expect(legend?.querySelectorAll(".chart-legend__item")).toHaveLength(2);
  });

  it("renders nothing when none of the requested keys have any data", () => {
    const { container } = render(<HealthTrendChart metrics={[]} keys={["weight_kg"]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when a metric is present but has zero points in range", () => {
    const { container } = render(
      <HealthTrendChart metrics={[metric("weight_kg", [])]} keys={["weight_kg"]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("formats a year-granularity tick as a plain year, not a month or day", () => {
    const ts = Date.UTC(2020, 5, 15);
    expect(formatAxisTick(ts, "year")).toBe("2020");
    expect(formatAxisTick(ts, "month")).toBe("Jun 2020");
    expect(formatAxisTick(ts, "day")).toBe("06-15");
  });
});
