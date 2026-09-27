import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BoulderingGoalProgressOut } from "../api/types";
import { BoulderingGoalsSection, goalTitle } from "./BoulderingGoalsSection";

const mockGoals = vi.fn();
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
const mockDelete = vi.fn();

vi.mock("../api/queries", () => ({
  useBoulderingGoals: (...args: unknown[]) => mockGoals(...args),
  useCreateBoulderingGoal: () => ({ mutate: mockCreate, isPending: false, error: null }),
  useUpdateBoulderingGoal: () => ({ mutate: mockUpdate, isPending: false, error: null }),
  useDeleteBoulderingGoal: () => ({ mutate: mockDelete, isPending: false }),
}));

function progress(
  id: number,
  overrides: Partial<BoulderingGoalProgressOut["goal"]> = {},
  extra: Partial<BoulderingGoalProgressOut> = {},
): BoulderingGoalProgressOut {
  return {
    goal: {
      id,
      period_type: "year",
      period_start: "2026",
      grade: 4,
      and_harder: false,
      target_count: 10,
      ...overrides,
    },
    period_end: "2026-12-31",
    daily: [
      { local_date: "2026-01-01", cumulative_count: 0 },
      { local_date: "2026-01-02", cumulative_count: 1 },
    ],
    target_per_day: 10 / 365,
    current_count: 1,
    target_as_of_today: 0.05,
    ahead_behind: 0.95,
    pct_complete: 0.1,
    ...extra,
  };
}

describe("goalTitle", () => {
  it("describes the grade scope", () => {
    const base = progress(1).goal;
    expect(goalTitle(base)).toBe("10 × V4");
    expect(goalTitle({ ...base, and_harder: true })).toBe("10 × V4 or harder");
    expect(goalTitle({ ...base, grade: null })).toBe("10 × any grade");
  });
});

describe("BoulderingGoalsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("asks for the goals of exactly this period", () => {
    mockGoals.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<BoulderingGoalsSection periodType="week" periodStart="2026-09-27" />);
    expect(mockGoals).toHaveBeenCalledWith("week", "2026-09-27");
  });

  it("lists every goal for the period with its progress", () => {
    mockGoals.mockReturnValue({
      data: [progress(1), progress(2, { grade: 5, target_count: 1 }, { current_count: 0, ahead_behind: -0.1, pct_complete: 0 })],
      isLoading: false,
      isError: false,
    });
    render(<BoulderingGoalsSection periodType="year" periodStart="2026" />);
    expect(screen.getByRole("heading", { name: "10 × V4" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "1 × V5" })).toBeInTheDocument();
    expect(screen.getByText(/of 10 completed \(10%\)/)).toBeInTheDocument();
    expect(screen.getByText("routes ahead of pace")).toBeInTheDocument();
    expect(screen.getByText("routes behind pace")).toBeInTheDocument();
  });

  it("adds a goal with the chosen grade, 'or harder' and count", () => {
    mockGoals.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<BoulderingGoalsSection periodType="month" periodStart="2026-10" />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add a bouldering goal" }));
    fireEvent.change(screen.getByLabelText("Grade"), { target: { value: "4" } });
    fireEvent.click(screen.getByLabelText("or harder"));
    fireEvent.change(screen.getByLabelText("Completed routes"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
    expect(mockCreate).toHaveBeenCalledWith(
      { period_type: "month", period_start: "2026-10", grade: 4, and_harder: true, target_count: 5 },
      expect.anything(),
    );
  });

  it("an any-grade goal never sends 'or harder'", () => {
    mockGoals.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<BoulderingGoalsSection periodType="year" periodStart="2026" />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add a bouldering goal" }));
    expect(screen.getByLabelText("or harder")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Completed routes"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
    expect(mockCreate).toHaveBeenCalledWith(
      { period_type: "year", period_start: "2026", grade: null, and_harder: false, target_count: 30 },
      expect.anything(),
    );
  });

  it("edits an existing goal in place and deletes one", () => {
    const g = progress(7);
    mockGoals.mockReturnValue({ data: [g], isLoading: false, isError: false });
    render(<BoulderingGoalsSection periodType="year" periodStart="2026" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Completed routes"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockUpdate).toHaveBeenCalledWith(
      {
        id: 7,
        period_type: "year",
        period_start: "2026",
        grade: 4,
        and_harder: false,
        target_count: 12,
      },
      expect.anything(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(mockDelete).toHaveBeenCalledWith(g.goal);
  });
});
