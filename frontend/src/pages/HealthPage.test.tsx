import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HealthPage } from "./HealthPage";

const mockUseHealthDashboard = vi.fn();
const mockUseSleep = vi.fn();

vi.mock("../api/queries", () => ({
  useHealthDashboard: (...args: unknown[]) => mockUseHealthDashboard(...args),
  useSleep: (...args: unknown[]) => mockUseSleep(...args),
}));

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
// inside today's own week for the "data present" tests to actually see it.
const TODAY = new Date().toISOString().slice(0, 10);

function daysBeforeToday(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

describe("HealthPage", () => {
  it("shows nothing to select when there's no data at all", () => {
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    render(<HealthPage />);
    expect(screen.queryByRole("button", { name: "Week" })).not.toBeInTheDocument();
    expect(screen.queryByText("Weight")).not.toBeInTheDocument();
    expect(screen.queryByText("Steps")).not.toBeInTheDocument();
  });

  it("lists every metric that has data, and switching selection swaps the visible chart", () => {
    mockUseHealthDashboard.mockReturnValue({
      ...EMPTY,
      data: {
        metrics: [
          { logical_metric: "weight_kg", last_observed: TODAY, daily: [dashboardDay(TODAY, 79.5)] },
          { logical_metric: "steps", last_observed: TODAY, daily: [dashboardDay(TODAY, 8000)] },
        ],
      },
    });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    render(<HealthPage />);

    const list = screen.getByRole("navigation", { name: "Metrics" });
    expect(list).toHaveTextContent("Weight");
    expect(list).toHaveTextContent("Steps");
    expect(list).not.toHaveTextContent("BMI");

    // Default selection is the first available metric.
    expect(screen.getByRole("heading", { name: "Weight" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Steps" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Steps" }));
    expect(screen.getByRole("heading", { name: "Steps" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Weight" })).not.toBeInTheDocument();
  });

  it("shows a chart only for a dashboard metric that actually has data", () => {
    mockUseHealthDashboard.mockReturnValue({
      ...EMPTY,
      data: {
        metrics: [
          {
            logical_metric: "weight_kg",
            last_observed: TODAY,
            daily: [dashboardDay(TODAY, 79.5)],
          },
        ],
      },
    });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    render(<HealthPage />);
    // getByRole, not getByText: ChartFullscreen's mobile tap-to-expand affordance renders each
    // panel title twice (a real button plus an aria-hidden static span) -- getByRole's
    // accessible-name computation correctly excludes the aria-hidden copy.
    expect(screen.getByRole("heading", { name: "Weight" })).toBeInTheDocument();
    expect(screen.queryByText("BMI")).not.toBeInTheDocument();
  });

  it("combines heart rate max/resting onto one chart card, not two", () => {
    mockUseHealthDashboard.mockReturnValue({
      ...EMPTY,
      data: {
        metrics: [
          {
            logical_metric: "max_heart_rate",
            last_observed: TODAY,
            daily: [dashboardDay(TODAY, 150)],
          },
          {
            logical_metric: "resting_heart_rate",
            last_observed: TODAY,
            daily: [dashboardDay(TODAY, 48)],
          },
        ],
      },
    });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    render(<HealthPage />);
    expect(screen.getAllByRole("heading", { name: "Heart rate" })).toHaveLength(1);
    expect(screen.getByText("Max")).toBeInTheDocument();
    expect(screen.getByText("Resting")).toBeInTheDocument();
  });

  it("switching resolution keeps the currently-viewed period instead of resetting to today", () => {
    mockUseHealthDashboard.mockReturnValue({
      ...EMPTY,
      data: {
        metrics: [
          {
            logical_metric: "weight_kg",
            last_observed: TODAY,
            // A second, much older day pushes dataStart back far enough that 12 "Earlier"
            // clicks in Month view never hit the "no more history" boundary partway through.
            daily: [dashboardDay(daysBeforeToday(800), 79.0), dashboardDay(TODAY, 79.5)],
          },
        ],
      },
    });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    render(<HealthPage />);

    fireEvent.click(screen.getByRole("button", { name: "Month" }));
    for (let i = 0; i < 12; i++) {
      fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
    }
    const lastYear = String(new Date().getUTCFullYear() - 1);

    fireEvent.click(screen.getByRole("button", { name: "Year" }));
    expect(screen.getByText(lastYear)).toBeInTheDocument();
    expect(screen.queryByText(String(new Date().getUTCFullYear()))).not.toBeInTheDocument();
  });

  it("builds the Sleep chart from GET /sleep, not the health dashboard", () => {
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    mockUseSleep.mockReturnValue({
      ...EMPTY,
      data: [
        { local_date: TODAY, start_time_utc: "x", end_time_utc: "y", total_sleep_s: 27000, sleep_score: 80, source: "garmin_connect", stages: [] },
      ],
    });
    render(<HealthPage />);
    expect(screen.getByRole("heading", { name: "Sleep time" })).toBeInTheDocument();
  });
});
