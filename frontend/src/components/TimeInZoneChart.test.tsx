import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityMetricOut } from "../api/types";
import { TimeInZoneChart } from "./TimeInZoneChart";

function metric(metric_key: string, value_num: number): ActivityMetricOut {
  return { metric_key, value_num, value_text: null, unit: null, source: "test" };
}

const realShape: ActivityMetricOut[] = [
  metric("fit.time_in_zone.time_in_hr_zone_0", 729.172),
  metric("fit.time_in_zone.time_in_hr_zone_1", 1544.902),
  metric("fit.time_in_zone.time_in_hr_zone_2", 77.001),
  metric("fit.time_in_zone.hr_zone_high_boundary_0", 87),
  metric("fit.time_in_zone.hr_zone_high_boundary_1", 106),
];

describe("TimeInZoneChart", () => {
  it("renders one row per zone with its bpm range", () => {
    render(<TimeInZoneChart metrics={realShape} />);
    expect(screen.getByText("Z0")).toBeInTheDocument();
    expect(screen.getByText("< 87")).toBeInTheDocument();
    expect(screen.getByText("87 – 106")).toBeInTheDocument();
  });

  it("renders nothing when the activity has no time-in-zone data", () => {
    const { container } = render(
      <TimeInZoneChart metrics={[metric("fit.session.avg_temperature", 20)]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when every zone is zero seconds (present but empty)", () => {
    const allZero: ActivityMetricOut[] = [
      metric("fit.time_in_zone.time_in_hr_zone_0", 0),
      metric("fit.time_in_zone.time_in_hr_zone_1", 0),
    ];
    const { container } = render(<TimeInZoneChart metrics={allZero} />);
    expect(container).toBeEmptyDOMElement();
  });
});
