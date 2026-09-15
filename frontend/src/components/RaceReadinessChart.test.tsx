import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RaceReadinessOut } from "../api/types";
import { RaceReadinessChart } from "./RaceReadinessChart";

const mockUseRaceReadiness = vi.fn();

vi.mock("../api/queries", () => ({
  useRaceReadiness: (...args: unknown[]) => mockUseRaceReadiness(...args),
}));

const EMPTY = { data: undefined, isLoading: false, isError: false };

const UNAVAILABLE: RaceReadinessOut = {
  available: false,
  race_id: null,
  race_name: null,
  race_local_date: null,
  race_distance_m: null,
  weekly_distance_target_m: null,
  long_run_target_m: null,
  as_of: null,
  current: null,
  predicted_duration_s: null,
  history: [],
  weekly_distance_series: [],
  long_run_series: [],
};

const AVAILABLE: RaceReadinessOut = {
  available: true,
  race_id: 1,
  race_name: "Test Marathon",
  race_local_date: "2026-12-06",
  race_distance_m: 42195.0,
  weekly_distance_target_m: 55000.0,
  long_run_target_m: 29000.0,
  as_of: "2026-09-13",
  current: {
    as_of: "2026-09-13",
    weekly_distance_compliance_pct: 66.4,
    long_run_compliance_pct: 57.6,
    readiness_pct: 62.9,
  },
  predicted_duration_s: 14547.9,
  history: [
    {
      as_of: "2026-08-30",
      weekly_distance_compliance_pct: 80.9,
      long_run_compliance_pct: 88.6,
      readiness_pct: 84.0,
    },
    {
      as_of: "2026-09-13",
      weekly_distance_compliance_pct: 66.4,
      long_run_compliance_pct: 57.6,
      readiness_pct: 62.9,
    },
  ],
  weekly_distance_series: [
    { week_start: "2026-08-31", distance_m: 40000.0 },
    { week_start: "2026-09-07", distance_m: 48000.0 },
  ],
  long_run_series: [
    { week_start: "2026-08-31", distance_m: 18000.0 },
    { week_start: "2026-09-07", distance_m: 22000.0 },
  ],
};

describe("RaceReadinessChart", () => {
  it("shows a loading spinner while fetching", () => {
    mockUseRaceReadiness.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<RaceReadinessChart />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows an error message on failure", () => {
    mockUseRaceReadiness.mockReturnValue({ ...EMPTY, isError: true });
    render(<RaceReadinessChart />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load race readiness.");
  });

  it("shows a prompt to add a race when there's no upcoming race", () => {
    mockUseRaceReadiness.mockReturnValue({ ...EMPTY, data: UNAVAILABLE });
    render(<RaceReadinessChart />);
    expect(screen.getByRole("heading", { name: "Race Readiness" })).toBeInTheDocument();
    expect(screen.getByText(/Add an upcoming race/)).toBeInTheDocument();
    expect(screen.queryByText("Readiness")).not.toBeInTheDocument();
  });

  it("shows readiness, weekly distance, long run, and prognosis stat tiles", () => {
    mockUseRaceReadiness.mockReturnValue({ ...EMPTY, data: AVAILABLE });
    render(<RaceReadinessChart />);
    // Every one of these labels also appears in the chart's own legend below the stat tiles --
    // scope to .stat-grid to avoid ambiguity between the two.
    const statGrid = document.querySelector(".stat-grid")!;
    expect(statGrid).toHaveTextContent("Readiness");
    expect(statGrid).toHaveTextContent("63"); // rounded 62.9
    expect(statGrid).toHaveTextContent("Weekly distance");
    expect(statGrid).toHaveTextContent("66");
    expect(statGrid).toHaveTextContent("Long run");
    expect(statGrid).toHaveTextContent("58");
    expect(statGrid).toHaveTextContent("Prognosis");
    expect(statGrid).toHaveTextContent("4:02:28");
  });

  it("mentions the race name and date in the explanatory copy", () => {
    mockUseRaceReadiness.mockReturnValue({ ...EMPTY, data: AVAILABLE });
    render(<RaceReadinessChart />);
    expect(screen.getByText(/Test Marathon/)).toBeInTheDocument();
    expect(screen.getByText(/2026-12-06/)).toBeInTheDocument();
  });

  it("omits the prognosis tile when there's no VDOT-based prediction", () => {
    mockUseRaceReadiness.mockReturnValue({
      ...EMPTY,
      data: { ...AVAILABLE, predicted_duration_s: null },
    });
    render(<RaceReadinessChart />);
    expect(screen.queryByText("Prognosis")).not.toBeInTheDocument();
  });

  it("shows the target distances on the weekly distance and long run stat tiles", () => {
    mockUseRaceReadiness.mockReturnValue({ ...EMPTY, data: AVAILABLE });
    render(<RaceReadinessChart />);
    const statGrid = document.querySelector(".stat-grid")!;
    expect(statGrid).toHaveTextContent("Target: 55.0 km/week");
    expect(statGrid).toHaveTextContent("Target: 29.0 km");
  });

  it("renders dedicated weekly distance and long run bar charts with a target reference line", () => {
    mockUseRaceReadiness.mockReturnValue({ ...EMPTY, data: AVAILABLE });
    const { container } = render(<RaceReadinessChart />);
    expect(
      screen.getByRole("heading", { name: "Weekly distance (last 182 days)" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Long run (last 70 days)" })).toBeInTheDocument();
    expect(container.querySelectorAll(".recharts-bar-rectangle").length).toBeGreaterThan(0);
    expect(container.querySelectorAll(".recharts-reference-line").length).toBeGreaterThan(0);
  });
});
