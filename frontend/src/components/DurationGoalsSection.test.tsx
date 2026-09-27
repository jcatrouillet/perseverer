import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DurationGoalProgressOut } from "../api/types";
import { DurationGoalsSection, durationGoalTitle } from "./DurationGoalsSection";

const mockGoals = vi.fn();
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
const mockDelete = vi.fn();
const mockRepeat = vi.fn();

vi.mock("../api/queries", () => ({
  useDurationGoals: (...args: unknown[]) => mockGoals(...args),
  useCreateDurationGoal: () => ({ mutate: mockCreate, isPending: false, error: null }),
  useUpdateDurationGoal: () => ({ mutate: mockUpdate, isPending: false, error: null }),
  useDeleteDurationGoal: () => ({ mutate: mockDelete, isPending: false }),
  useRepeatDurationGoal: () => ({ mutate: mockRepeat, isPending: false, error: null }),
}));

function progress(
  id: number,
  sport: string | null,
  target_duration_s = 10_800,
  extra: Partial<DurationGoalProgressOut> = {},
): DurationGoalProgressOut {
  return {
    goal: {
      id,
      period_type: "week",
      period_start: "2026-09-28",
      sport,
      target_duration_s,
    },
    period_end: "2026-10-04",
    daily: [
      { local_date: "2026-09-28", cumulative_duration_s: 0 },
      { local_date: "2026-09-29", cumulative_duration_s: 3600 },
    ],
    target_per_day_s: target_duration_s / 7,
    current_duration_s: 3600,
    target_as_of_today_s: 3600,
    ahead_behind_s: 0,
    pct_complete: 3600 / target_duration_s,
    ...extra,
  };
}

describe("durationGoalTitle", () => {
  it("names the amount of time and the sport", () => {
    expect(durationGoalTitle(progress(1, "yoga").goal)).toBe("3h 0m yoga");
    expect(durationGoalTitle(progress(1, "strength_training", 5400).goal)).toBe(
      "1h 30m strength training",
    );
    expect(durationGoalTitle(progress(1, null, 36_000).goal)).toBe("10h 0m all sports");
  });
});

describe("DurationGoalsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("asks for the goals of exactly this period", () => {
    mockGoals.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<DurationGoalsSection periodType="week" periodStart="2026-09-28" />);
    expect(mockGoals).toHaveBeenCalledWith("week", "2026-09-28");
    expect(screen.getByText(/No time goal for this period yet/)).toBeInTheDocument();
  });

  it("lists every goal with its progress", () => {
    mockGoals.mockReturnValue({
      data: [
        progress(1, "yoga"),
        progress(2, null, 36_000, { ahead_behind_s: -1800, current_duration_s: 0 }),
      ],
      isLoading: false,
      isError: false,
    });
    render(<DurationGoalsSection periodType="week" periodStart="2026-09-28" />);
    expect(screen.getByRole("heading", { name: "3h 0m yoga" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "10h 0m all sports" })).toBeInTheDocument();
    expect(screen.getByText("ahead of pace")).toBeInTheDocument();
    expect(screen.getByText("behind pace")).toBeInTheDocument();
  });

  it("offers every sport, not just distance ones, and creates the goal in seconds", () => {
    mockGoals.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<DurationGoalsSection periodType="month" periodStart="2026-10" />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add a time goal" }));
    const options = [...(screen.getByLabelText("Sport") as HTMLSelectElement).options].map(
      (o) => o.value,
    );
    for (const sport of ["yoga", "strength_training", "hiit", "rock_climbing", "running"]) {
      expect(options).toContain(sport);
    }
    fireEvent.change(screen.getByLabelText("Sport"), { target: { value: "yoga" } });
    fireEvent.change(screen.getByLabelText("Target time (hours)"), { target: { value: "2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
    expect(mockCreate).toHaveBeenCalledWith(
      { period_type: "month", period_start: "2026-10", sport: "yoga", target_duration_s: 9000 },
      expect.anything(),
    );
  });

  it("'All sports' sends a null sport", () => {
    mockGoals.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<DurationGoalsSection periodType="year" periodStart="2026" />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add a time goal" }));
    fireEvent.change(screen.getByLabelText("Target time (hours)"), { target: { value: "300" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
    expect(mockCreate).toHaveBeenCalledWith(
      { period_type: "year", period_start: "2026", sport: null, target_duration_s: 1_080_000 },
      expect.anything(),
    );
  });

  it("a new weekly goal can be repeated; month/year and edit have no repeat field", () => {
    mockGoals.mockReturnValue({ data: [], isLoading: false, isError: false });
    const { unmount } = render(<DurationGoalsSection periodType="week" periodStart="2026-09-28" />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add a time goal" }));
    fireEvent.change(screen.getByLabelText("Target time (hours)"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText(/Repeat for/), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
    expect(mockRepeat).toHaveBeenCalledWith(
      {
        period_type: "week",
        period_start: "2026-09-28",
        sport: null,
        target_duration_s: 10_800,
        weeks: 6,
      },
      expect.anything(),
    );
    expect(mockCreate).not.toHaveBeenCalled();
    unmount();

    render(<DurationGoalsSection periodType="month" periodStart="2026-10" />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add a time goal" }));
    expect(screen.queryByLabelText(/Repeat for/)).not.toBeInTheDocument();
  });

  it("edits a goal in place and deletes one", () => {
    const g = progress(7, "yoga");
    mockGoals.mockReturnValue({ data: [g], isLoading: false, isError: false });
    render(<DurationGoalsSection periodType="week" periodStart="2026-09-28" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.queryByLabelText(/Repeat for/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Target time (hours)"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockUpdate).toHaveBeenCalledWith(
      {
        id: 7,
        period_type: "week",
        period_start: "2026-09-28",
        sport: "yoga",
        target_duration_s: 14_400,
      },
      expect.anything(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(mockDelete).toHaveBeenCalledWith(g.goal);
  });
});
