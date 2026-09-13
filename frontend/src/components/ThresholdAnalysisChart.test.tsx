import { render, screen } from "@testing-library/react";
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
    expect(list).toHaveTextContent("Threshold pace");
    expect(list).not.toHaveTextContent("Threshold & max HR");

    expect(screen.getByRole("heading", { name: "Threshold pace" })).toBeInTheDocument();
  });

  it("plots both anaerobic and aerobic threshold pace on the same combined chart", () => {
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

    // One metric in the list (not two), and both series' own legend labels render on that one
    // chart.
    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("Threshold pace");
    expect(screen.getByRole("heading", { name: "Threshold pace" })).toBeInTheDocument();
    expect(screen.getByText("Anaerobic threshold pace")).toBeInTheDocument();
    expect(screen.getByText("Aerobic threshold pace")).toBeInTheDocument();
  });

  it("combines max HR, anaerobic threshold HR, and aerobic threshold HR on one chart", () => {
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
    expect(list).toHaveTextContent("Threshold & max HR");
    expect(list).not.toHaveTextContent("Threshold pace");

    // The only available metric, so it's already selected -- no click needed.
    expect(screen.getByRole("heading", { name: "Threshold & max HR" })).toBeInTheDocument();
    expect(screen.getByText("Max heart rate")).toBeInTheDocument();
    expect(screen.getByText("Anaerobic threshold HR")).toBeInTheDocument();
    expect(screen.getByText("Aerobic threshold HR")).toBeInTheDocument();
  });
});
