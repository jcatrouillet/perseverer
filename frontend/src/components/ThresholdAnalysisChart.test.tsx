import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PerformanceDailyRollupOut } from "../api/types";
import { ThresholdAnalysisChart } from "./ThresholdAnalysisChart";

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
    aerobic_threshold_pace_s_per_km: null,
    aerobic_threshold_hr_bpm: null,
    aerobic_threshold_hr_source: null,
    predicted_5k_s: null,
    predicted_10k_s: null,
    predicted_half_marathon_s: null,
    predicted_marathon_s: null,
    ...overrides,
  } satisfies PerformanceDailyRollupOut;
}

const EMPTY = { data: undefined, isLoading: false, isError: false };
const TODAY = new Date().toISOString().slice(0, 10);

describe("ThresholdAnalysisChart", () => {
  it("shows a loading spinner while fetching", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<ThresholdAnalysisChart />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows nothing to select when there is no data anywhere", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, data: [] });
    render(<ThresholdAnalysisChart />);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("lists only the metrics that actually have data, and switches on click", () => {
    mockUsePerformance.mockReturnValue({
      ...EMPTY,
      data: [row(TODAY, { threshold_pace_s_per_km: 255 })],
    });
    render(<ThresholdAnalysisChart />);

    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("Anaerobic threshold pace");
    expect(list).not.toHaveTextContent("Aerobic threshold pace");
    expect(list).not.toHaveTextContent("Anaerobic threshold & max HR");

    expect(screen.getByRole("heading", { name: "Anaerobic threshold pace" })).toBeInTheDocument();
  });

  it("shows the aerobic threshold pace as its own metric, independent of the anaerobic one", () => {
    mockUsePerformance.mockReturnValue({
      ...EMPTY,
      data: [
        row(TODAY, {
          threshold_pace_s_per_km: 255,
          aerobic_threshold_pace_s_per_km: 297,
        }),
      ],
    });
    render(<ThresholdAnalysisChart />);

    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("Anaerobic threshold pace");
    expect(list).toHaveTextContent("Aerobic threshold pace");

    fireEvent.click(screen.getByRole("button", { name: "Aerobic threshold pace" }));
    expect(screen.getByRole("heading", { name: "Aerobic threshold pace" })).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Anaerobic threshold pace" }),
    ).not.toBeInTheDocument();
  });

  it("shows anaerobic and aerobic threshold & max HR as two independent combined metrics", () => {
    mockUsePerformance.mockReturnValue({
      ...EMPTY,
      data: [
        row(TODAY, {
          max_hr_bpm: 188,
          threshold_hr_bpm: 165,
          threshold_hr_source: "empirical",
          aerobic_threshold_hr_bpm: 155,
          aerobic_threshold_hr_source: "empirical",
        }),
      ],
    });
    render(<ThresholdAnalysisChart />);

    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("Anaerobic threshold & max HR");
    expect(list).toHaveTextContent("Aerobic threshold & max HR");
    expect(list).not.toHaveTextContent("Threshold pace");

    fireEvent.click(screen.getByRole("button", { name: "Aerobic threshold & max HR" }));
    expect(
      screen.getByRole("heading", { name: "Aerobic threshold & max HR" }),
    ).toBeInTheDocument();
  });
});
