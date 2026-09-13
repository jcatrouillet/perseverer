import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PerformanceDailyRollupOut } from "../api/types";
import { Vo2maxChart } from "./Vo2maxChart";

const mockUsePerformance = vi.fn();

vi.mock("../api/queries", () => ({
  usePerformance: (...args: unknown[]) => mockUsePerformance(...args),
}));

function row(local_date: string, rolling_vdot: number | null): PerformanceDailyRollupOut {
  return {
    local_date,
    rolling_vdot,
    max_hr_bpm: null,
    max_hr_source: null,
    threshold_pace_s_per_km: null,
    threshold_hr_bpm: null,
    threshold_hr_source: null,
    predicted_5k_s: null,
    predicted_10k_s: null,
    predicted_half_marathon_s: null,
    predicted_marathon_s: null,
  };
}

const EMPTY = { data: undefined, isLoading: false, isError: false };
const TODAY = new Date().toISOString().slice(0, 10);

describe("Vo2maxChart", () => {
  it("shows a loading spinner while fetching", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<Vo2maxChart />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders nothing when there's no VO2max data anywhere", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, data: [row(TODAY, null)] });
    const { container } = render(<Vo2maxChart />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the chart and resolution controls (including Custom) once VO2max data exists", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, data: [row(TODAY, 52.3)] });
    render(<Vo2maxChart />);
    expect(screen.getByRole("heading", { name: "VO2max" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Week" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Custom" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Metrics" })).not.toBeInTheDocument();
  });

  it("switching to Custom reveals date pickers instead of prev/next", () => {
    mockUsePerformance.mockReturnValue({ ...EMPTY, data: [row(TODAY, 52.3)] });
    render(<Vo2maxChart />);
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    expect(screen.getByLabelText("Custom range start")).toBeInTheDocument();
    expect(screen.getByLabelText("Custom range end")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Earlier" })).not.toBeInTheDocument();
  });
});
