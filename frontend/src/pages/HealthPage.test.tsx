import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { localIsoDate } from "../dateUtils";
import { HealthPage } from "./HealthPage";

const mockUseHealthDashboard = vi.fn();
const mockUseSleep = vi.fn();
const mockUseBloodTests = vi.fn();

vi.mock("../api/queries", () => ({
  useHealthDashboard: (...args: unknown[]) => mockUseHealthDashboard(...args),
  useSleep: (...args: unknown[]) => mockUseSleep(...args),
  // BloodTestsPanel's own hooks -- this page renders it unconditionally alongside the metric
  // explorer, so every test here needs it mocked too, even ones that don't care about it.
  useBloodTests: (...args: unknown[]) => mockUseBloodTests(...args),
  useCreateBloodTestBatch: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useUpdateBloodTestResult: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteBloodTestResult: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteBloodTestPanel: () => ({ mutate: vi.fn(), isPending: false }),
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
const TODAY = localIsoDate();

function daysBeforeToday(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return localIsoDate(d);
}

describe("HealthPage", () => {
  beforeEach(() => {
    // BloodTestsPanel renders unconditionally below the metric explorer -- not this describe
    // block's own concern (see BloodTestsPanel.test.tsx), so give it an inert empty default.
    mockUseBloodTests.mockReturnValue({ data: [], isLoading: false, isError: false });
  });

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

    // Steps sits in the collapsed "Activity & sleep" group until it is opened.
    fireEvent.click(screen.getByRole("button", { name: /Activity & sleep/ }));
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

  it("switching to Custom shows date pickers instead of prev/next, seeded with a default range", () => {
    mockUseHealthDashboard.mockReturnValue({
      ...EMPTY,
      data: {
        metrics: [
          {
            logical_metric: "weight_kg",
            last_observed: TODAY,
            daily: [dashboardDay(daysBeforeToday(800), 79.0), dashboardDay(TODAY, 79.5)],
          },
        ],
      },
    });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    render(<HealthPage />);

    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    expect(screen.queryByRole("button", { name: "Earlier" })).not.toBeInTheDocument();
    const start = screen.getByLabelText("Custom range start") as HTMLInputElement;
    const end = screen.getByLabelText("Custom range end") as HTMLInputElement;
    expect(end.value).toBe(TODAY);
    expect(start.value).not.toBe("");

    fireEvent.change(end, { target: { value: daysBeforeToday(5) } });
    expect((screen.getByLabelText("Custom range end") as HTMLInputElement).value).toBe(
      daysBeforeToday(5),
    );
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

  it("lists each blood marker under Blood tests > category and charts the selected one", () => {
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    mockUseBloodTests.mockReturnValue({
      data: [
        {
          id: 1,
          local_date: "2015-10-14",
          marker: "ALT",
          value_num: 81,
          unit: "U/L",
          reference_low: 15,
          reference_high: 60,
          lab_name: null,
          notes: null,
          created_at: "2015-10-14T00:00:00",
          updated_at: "2015-10-14T00:00:00",
        },
      ],
      isLoading: false,
      isError: false,
    });
    render(<HealthPage />);
    // Blood tests is the only group here, so it is already open; open the category under it.
    fireEvent.click(screen.getByRole("button", { name: /Liver & pancreas/ }));
    fireEvent.click(screen.getByRole("button", { name: "ALT" }));
    expect(screen.getByRole("button", { name: "ALT" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("heading", { name: /ALT/ })).toBeInTheDocument();
    expect(screen.getByText(/1 result, 1 outside the range/)).toBeInTheDocument();
    // A blood marker is its own all-time history, so the window controls are not shown for it.
    expect(screen.queryByRole("button", { name: "Week" })).not.toBeInTheDocument();
  });

  it("groups the body metrics into collapsible categories", () => {
    mockUseHealthDashboard.mockReturnValue({
      ...EMPTY,
      data: {
        metrics: [
          { logical_metric: "weight_kg", last_observed: TODAY, daily: [dashboardDay(TODAY, 80)] },
        ],
      },
    });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    render(<HealthPage />);
    expect(screen.getByRole("button", { name: /Body composition/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("navigation", { name: "Metrics" })).toHaveTextContent("Weight");
    expect(screen.getByRole("button", { name: /Blood tests/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("offers Add or manage results even before any blood test exists", () => {
    mockUseHealthDashboard.mockReturnValue({ ...EMPTY, data: { metrics: [] } });
    mockUseSleep.mockReturnValue({ ...EMPTY, data: [] });
    render(<HealthPage />);
    expect(screen.getByRole("button", { name: "Add or manage results" })).toBeInTheDocument();
  });
});
