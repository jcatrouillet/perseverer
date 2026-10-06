import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActivitySummary } from "../api/types";
import { EddingtonChart } from "./EddingtonChart";

const mockUseAllActivities = vi.fn();

vi.mock("../api/queries", () => ({
  useAllActivities: (...args: unknown[]) => mockUseAllActivities(...args),
}));

function run(local_date: string, distanceKm: number): ActivitySummary {
  return {
    id: `${local_date}-${distanceKm}`,
    start_time_utc: `${local_date}T10:00:00Z`,
    utc_offset_s: 0,
    local_date,
    sport: "running",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m: distanceKm * 1000,
    elevation_gain_m: null,
    max_altitude_m: null,
    calories: null,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    vdot: null,
  } as ActivitySummary;
}

const EMPTY = { data: undefined, isLoading: false, isError: false };

describe("EddingtonChart", () => {
  it("shows a loading spinner while fetching", () => {
    mockUseAllActivities.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<EddingtonChart />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders nothing when there are no running activities", () => {
    mockUseAllActivities.mockReturnValue({ ...EMPTY, data: [] });
    const { container } = render(<EddingtonChart />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows one row per year with its own Eddington number and progress", () => {
    mockUseAllActivities.mockReturnValue({
      ...EMPTY,
      data: [
        run("2025-01-01", 10),
        run("2025-01-02", 8),
        run("2025-01-03", 8),
        run("2024-01-01", 1),
      ],
    });
    render(<EddingtonChart />);

    expect(screen.getByRole("heading", { name: "Eddington number" })).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    // Header + two year rows.
    expect(rows).toHaveLength(3);

    const row2025 = screen.getByText("2025").closest("tr")!;
    expect(row2025).toHaveTextContent("3"); // Eddington number
    expect(row2025).toHaveTextContent("3"); // total runs
    expect(row2025).toHaveTextContent("3 / 4 runs of 4+ km logged");

    const row2024 = screen.getByText("2024").closest("tr")!;
    expect(row2024).toHaveTextContent("1");
  });

  it("lists the most recent year first", () => {
    mockUseAllActivities.mockReturnValue({
      ...EMPTY,
      data: [run("2023-01-01", 5), run("2025-01-01", 5), run("2024-01-01", 5)],
    });
    render(<EddingtonChart />);
    const years = screen
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.textContent!.slice(0, 4));
    expect(years).toEqual(["2025", "2024", "2023"]);
  });

  it("shows the current year's bar chart when this year has qualifying runs", () => {
    const currentYear = new Date().getFullYear();
    mockUseAllActivities.mockReturnValue({
      ...EMPTY,
      data: [run(`${currentYear}-01-01`, 5), run(`${currentYear}-01-02`, 3)],
    });
    render(<EddingtonChart />);
    expect(screen.getByText(new RegExp(`${currentYear}: bar height`))).toBeInTheDocument();
  });

  it("omits the current year's bar chart when this year has no qualifying runs", () => {
    mockUseAllActivities.mockReturnValue({
      ...EMPTY,
      data: [run("2019-01-01", 5), run("2019-01-02", 3)],
    });
    render(<EddingtonChart />);
    expect(screen.queryByText(/bar height/)).not.toBeInTheDocument();
  });
});
