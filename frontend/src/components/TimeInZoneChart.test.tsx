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

  it("scales bar widths against the total time in zone, not the single largest zone", () => {
    const { container } = render(<TimeInZoneChart metrics={realShape} />);
    const fills = container.querySelectorAll<HTMLElement>(".time-in-zone__bar-fill");
    // Zone 1 (1544.902s) is the largest zone but only ~66% of the 2351.075s total -- scaling
    // against the max zone (the pre-fix behavior) would render it at 100% width, which made
    // every other real, nonzero zone shrink to a near-invisible sliver.
    expect(fills[1]!.style.width).not.toBe("100%");
    expect(screen.getByText(/66%/)).toBeInTheDocument();
  });

  it("renders nothing when every zone is zero seconds (present but empty)", () => {
    const allZero: ActivityMetricOut[] = [
      metric("fit.time_in_zone.time_in_hr_zone_0", 0),
      metric("fit.time_in_zone.time_in_hr_zone_1", 0),
    ];
    const { container } = render(<TimeInZoneChart metrics={allZero} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("uses the configured boundaries against the raw stream instead of device zones, when given both", () => {
    const timestamps = [
      "2026-01-01T00:00:00Z",
      "2026-01-01T00:00:10Z",
      "2026-01-01T00:00:20Z",
    ];
    render(
      <TimeInZoneChart
        metrics={realShape}
        heartRateStream={[110, 150, 150]}
        timestamps={timestamps}
        configuredZoneBoundaries={[120, 140, 155, 170]}
      />,
    );
    // The configured-boundary path produces Z1-Z5 (1-indexed), not the device path's Z0 label.
    expect(screen.queryByText("Z0")).not.toBeInTheDocument();
    expect(screen.getByText("Z1")).toBeInTheDocument();
    expect(screen.getByText("< 120")).toBeInTheDocument();
  });

  it("falls back to device-reported zones when the stream/boundaries aren't available", () => {
    render(
      <TimeInZoneChart metrics={realShape} heartRateStream={null} timestamps={null} configuredZoneBoundaries={null} />,
    );
    expect(screen.getByText("Z0")).toBeInTheDocument();
  });
});
