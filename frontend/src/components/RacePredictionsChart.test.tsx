import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PerformanceDailyRollupOut } from "../api/types";
import { formatRaceTime, RacePredictionsChart } from "./RacePredictionsChart";

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

describe("formatRaceTime", () => {
  it("formats under an hour as M:SS", () => {
    expect(formatRaceTime(1290)).toBe("21:30");
  });

  it("formats an hour or more as H:MM:SS", () => {
    expect(formatRaceTime(9045)).toBe("2:30:45");
  });
});

describe("RacePredictionsChart", () => {
  it("shows a loading spinner while fetching", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<RacePredictionsChart />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows nothing to select when there is no data anywhere", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, data: [] });
    render(<RacePredictionsChart />);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("lists only the distances that actually have a prediction, and switches on click", () => {
    mockUsePerformance.mockReturnValue({
      ...EMPTY,
      data: [row(TODAY, { predicted_5k_s: 1200, predicted_marathon_s: 11000 })],
    });
    render(<RacePredictionsChart />);

    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("5K");
    expect(list).toHaveTextContent("Marathon");
    expect(list).not.toHaveTextContent("10K");
    expect(list).not.toHaveTextContent("Half marathon");

    expect(screen.getByRole("heading", { name: "5K" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Marathon" }));
    expect(screen.getByRole("heading", { name: "Marathon" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "5K" })).not.toBeInTheDocument();
  });
});
