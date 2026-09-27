import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DurationGoalProgressOut } from "../api/types";
import { formatTargetHM, parseTargetHM } from "./DurationGoalForm";
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

describe("target time as h:mm", () => {
  it("parses hours and minutes, and a bare number as whole hours", () => {
    expect(parseTargetHM("3:30")).toBe(12_600);
    expect(parseTargetHM("0:45")).toBe(2_700);
    expect(parseTargetHM(" 12:05 ")).toBe(43_500);
    expect(parseTargetHM("3")).toBe(10_800);
  });

  it("rejects what is not h:mm", () => {
    for (const bad of ["", "0", "0:00", "3:60", "3:5", "2.5", "-1:00", "1:30:00", "abc"]) {
      expect(parseTargetHM(bad)).toBeNull();
    }
  });

  it("formats a stored target back to h:mm", () => {
    expect(formatTargetHM(10_800)).toBe("3:00");
    expect(formatTargetHM(5_400)).toBe("1:30");
    expect(formatTargetHM(2_700)).toBe("0:45");
    expect(formatTargetHM(1_080_000)).toBe("300:00");
    expect(formatTargetHM(7_199)).toBe("2:00");
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
    fireEvent.change(screen.getByLabelText("Target time (h:mm)"), { target: { value: "2:30" } });
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
    fireEvent.change(screen.getByLabelText("Target time (h:mm)"), { target: { value: "300" } });
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
    fireEvent.change(screen.getByLabelText("Target time (h:mm)"), { target: { value: "3:00" } });
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
    expect(screen.getByLabelText("Target time (h:mm)")).toHaveValue("3:00");
    fireEvent.change(screen.getByLabelText("Target time (h:mm)"), { target: { value: "4:15" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockUpdate).toHaveBeenCalledWith(
      {
        id: 7,
        period_type: "week",
        period_start: "2026-09-28",
        sport: "yoga",
        target_duration_s: 15_300,
      },
      expect.anything(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(mockDelete).toHaveBeenCalledWith(g.goal);
  });
});
