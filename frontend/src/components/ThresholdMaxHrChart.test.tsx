import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PerformanceDailyRollupOut } from "../api/types";
import { ThresholdMaxHrChart } from "./ThresholdMaxHrChart";

const mockUsePerformance = vi.fn();

vi.mock("../api/queries", () => ({
  usePerformance: (...args: unknown[]) => mockUsePerformance(...args),
}));

function row(local_date: string, overrides: Partial<PerformanceDailyRollupOut> = {}) {
  return {
    local_date,
    rolling_vdot: null,
    max_hr_bpm: null,
    max_hr_source: null,
    threshold_pace_s_per_km: null,
    threshold_hr_bpm: null,
    threshold_hr_source: null,
    predicted_5k_s: null,
    predicted_10k_s: null,
    predicted_half_marathon_s: null,
    predicted_marathon_s: null,
    ...overrides,
  } satisfies PerformanceDailyRollupOut;
}

const EMPTY = { data: undefined, isLoading: false, isError: false };
const TODAY = new Date().toISOString().slice(0, 10);

describe("ThresholdMaxHrChart", () => {
  it("shows a loading spinner while fetching", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<ThresholdMaxHrChart />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows nothing to select when there is no data anywhere", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, data: [] });
    render(<ThresholdMaxHrChart />);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("lists only the metrics that actually have data, and switches on click", () => {
    mockUsePerformance.mockReturnValue({
      ...EMPTY,
      data: [row(TODAY, { threshold_pace_s_per_km: 255 })],
    });
    render(<ThresholdMaxHrChart />);

    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("Threshold pace");
    expect(list).not.toHaveTextContent("Threshold & max HR");

    expect(screen.getByRole("heading", { name: "Threshold pace" })).toBeInTheDocument();
  });

  it("shows threshold & max HR as one combined metric when that data is present", () => {
    mockUsePerformance.mockReturnValue({
      ...EMPTY,
      data: [row(TODAY, { max_hr_bpm: 188, threshold_hr_bpm: 165, threshold_hr_source: "empirical" })],
    });
    render(<ThresholdMaxHrChart />);

    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("Threshold & max HR");
    expect(list).not.toHaveTextContent("Threshold pace");
    expect(screen.getByRole("heading", { name: "Threshold & max HR" })).toBeInTheDocument();
  });

  it("switches between metrics on click when both have data", () => {
    mockUsePerformance.mockReturnValue({
      ...EMPTY,
      data: [
        row(TODAY, {
          threshold_pace_s_per_km: 255,
          threshold_hr_bpm: 165,
          threshold_hr_source: "empirical",
          max_hr_bpm: 188,
        }),
      ],
    });
    render(<ThresholdMaxHrChart />);

    expect(screen.getByRole("heading", { name: "Threshold pace" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Threshold & max HR" }));
    expect(screen.getByRole("heading", { name: "Threshold & max HR" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Threshold pace" })).not.toBeInTheDocument();
  });
});
