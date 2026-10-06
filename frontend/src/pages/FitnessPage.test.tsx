import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { FitnessDailyRollupOut } from "../api/types";
import { localIsoDate } from "../dateUtils";
import { FitnessPage } from "./FitnessPage";

const mockUseFitness = vi.fn();
const mockUseHealthDashboard = vi.fn();

vi.mock("../api/queries", () => ({
  useFitness: (...args: unknown[]) => mockUseFitness(...args),
  useHealthDashboard: (...args: unknown[]) => mockUseHealthDashboard(...args),
}));

function fitnessRow(
  local_date: string,
  ctl: number,
  atl: number,
  tsb: number,
): FitnessDailyRollupOut {
  return { local_date, training_load: 100, ctl, atl, tsb };
}

function dashboardDay(local_date: string, value: number) {
  return {
    local_date,
    value_sum: value,
    value_avg: value,
    value_min: value,
    value_max: value,
    value_last: value,
    n_observations: 1,
    source_metric_key: "test",
  };
}

const EMPTY = { data: undefined, isLoading: false, isError: false };
// The page's own default resolution is "week" anchored on today -- sample data has to fall
// inside today's own week for the "data present" tests to actually see it, so dates are derived
// from the real clock rather than a fixed string that would drift outside that window over time.
const TODAY = localIsoDate();

function daysBeforeToday(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return localIsoDate(d);
}

describe("FitnessPage", () => {
  it("shows a loading spinner while fetching", () => {
    mockUseFitness.mockReturnValue({ ...EMPTY, isLoading: true });
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<FitnessPage />);
    expect(screen.getByText("Fitness & Form")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows nothing to select when there is no data anywhere", () => {
    mockUseFitness.mockReturnValue({ ...EMPTY, data: [] });
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    render(<FitnessPage />);
    expect(screen.queryByRole("button", { name: "Week" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("lists only the metrics that actually have data, and renders the resolution controls", () => {
    mockUseFitness.mockReturnValue({
      ...EMPTY,
      data: [fitnessRow(TODAY, 40, 35, 5)],
    });
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    render(<FitnessPage />);
    expect(screen.getByRole("button", { name: "Week" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Month" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Year" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All time" })).toBeInTheDocument();

    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("Fitness, Fatigue & Form");
    expect(list).not.toHaveTextContent("VO2max");
    expect(list).not.toHaveTextContent("HRV");
    expect(list).not.toHaveTextContent("Lactate threshold");

    // getByRole, not getByText: ChartFullscreen's mobile tap-to-expand affordance renders each
    // panel title twice (a real button plus an aria-hidden static span) -- getByRole's
    // accessible-name computation correctly excludes the aria-hidden copy.
    expect(screen.getByRole("heading", { name: "Fitness, Fatigue & Form" })).toBeInTheDocument();
  });

  it("selects the first available metric by default and switches on click", () => {
    mockUseFitness.mockReturnValue({
      ...EMPTY,
      data: [fitnessRow(TODAY, 40, 35, 5)],
    });
    mockUseHealthDashboard.mockReturnValue({
      ...EMPTY,
      data: {
        metrics: [
          { logical_metric: "vo2max", last_observed: TODAY, daily: [dashboardDay(TODAY, 52)] },
        ],
      },
    });
    render(<FitnessPage />);

    // Default selection is the first metric in the list -- its chart shows, the other doesn't.
    expect(screen.getByRole("heading", { name: "Fitness, Fatigue & Form" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "VO2max" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "VO2max" }));
    expect(screen.getByRole("heading", { name: "VO2max" })).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Fitness, Fatigue & Form" }),
    ).not.toBeInTheDocument();
  });

  it("switching to a different resolution updates the window label", () => {
    mockUseFitness.mockReturnValue({
      ...EMPTY,
      data: [fitnessRow(TODAY, 40, 35, 5)],
    });
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    render(<FitnessPage />);
    fireEvent.click(screen.getByRole("button", { name: "Year" }));
    const thisYear = String(new Date().getUTCFullYear());
    expect(screen.getByText(thisYear)).toBeInTheDocument();
  });

  it("switching resolution keeps the currently-viewed period instead of resetting to today", () => {
    mockUseFitness.mockReturnValue({
      ...EMPTY,
      // A second, much older row pushes dataStart back far enough that 12 "Earlier" clicks in
      // Month view never hit the "no more history" boundary and get disabled partway through.
      data: [fitnessRow(daysBeforeToday(800), 40, 35, 5), fitnessRow(TODAY, 40, 35, 5)],
    });
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    render(<FitnessPage />);

    fireEvent.click(screen.getByRole("button", { name: "Month" }));
    // Navigate back a full year, one month at a time, so the anchor sits in the same calendar
    // month twelve months ago -- always a different year than today's, whatever today's date is.
    for (let i = 0; i < 12; i++) {
      fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
    }
    const lastYear = String(new Date().getUTCFullYear() - 1);

    // Switching resolution must re-derive the new window from that same anchor date, not reset
    // it back to today -- Year should land on last year, not this one.
    fireEvent.click(screen.getByRole("button", { name: "Year" }));
    expect(screen.getByText(lastYear)).toBeInTheDocument();
    expect(screen.queryByText(String(new Date().getUTCFullYear()))).not.toBeInTheDocument();
  });

  it("switching to Custom shows date pickers instead of prev/next, seeded with a default range", () => {
    mockUseFitness.mockReturnValue({
      ...EMPTY,
      data: [fitnessRow(daysBeforeToday(800), 40, 35, 5), fitnessRow(TODAY, 40, 35, 5)],
    });
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    render(<FitnessPage />);

    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    expect(screen.queryByRole("button", { name: "Earlier" })).not.toBeInTheDocument();
    const start = screen.getByLabelText("Custom range start") as HTMLInputElement;
    const end = screen.getByLabelText("Custom range end") as HTMLInputElement;
    expect(end.value).toBe(TODAY);
    expect(start.value).not.toBe("");

    fireEvent.change(start, { target: { value: daysBeforeToday(10) } });
    expect((screen.getByLabelText("Custom range start") as HTMLInputElement).value).toBe(
      daysBeforeToday(10),
    );
  });
});
