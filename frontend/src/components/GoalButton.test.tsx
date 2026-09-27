import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { GoalProgressOut } from "../api/types";
import { GoalButton } from "./GoalButton";

const mockUseGoalProgress = vi.fn();
const mockSetGoalMutate = vi.fn();
const mockDeleteGoalMutate = vi.fn();
const mockRepeatGoalMutate = vi.fn();

vi.mock("../api/queries", () => ({
  useGoalProgress: (...args: unknown[]) => mockUseGoalProgress(...args),
  // The bouldering half of the same popup.
  useBoulderingGoals: () => ({ data: [], isLoading: false, isError: false }),
  useCreateBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useUpdateBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false }),
  useSetGoal: () => ({ mutate: mockSetGoalMutate, isPending: false, isError: false }),
  useDeleteGoal: () => ({ mutate: mockDeleteGoalMutate }),
  useRepeatGoal: () => ({ mutate: mockRepeatGoalMutate, isPending: false, isError: false }),
  useRepeatBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));

const NO_GOAL: GoalProgressOut = {
  available: false,
  goal: null,
  period_end: null,
  daily: [],
  target_per_day_m: null,
  current_distance_m: null,
  target_distance_as_of_today_m: null,
  ahead_behind_m: null,
  pct_complete: null,
};

const WITH_GOAL: GoalProgressOut = {
  available: true,
  goal: {
    id: 1,
    period_type: "year",
    period_start: "2026",
    sport: "running",
    target_distance_m: 2_000_000,
  },
  period_end: "2026-12-31",
  daily: [
    { local_date: "2026-01-01", cumulative_distance_m: 0 },
    { local_date: "2026-08-17", cumulative_distance_m: 1_287_000 },
  ],
  target_per_day_m: 5479.45,
  current_distance_m: 1_287_000,
  target_distance_as_of_today_m: 1_254_600,
  ahead_behind_m: 32_400,
  pct_complete: 0.6435,
};

describe("GoalButton", () => {
  it("is one button labelled Goals, whether or not a goal is set", () => {
    mockUseGoalProgress.mockReturnValue({ data: NO_GOAL, isLoading: false, isError: false });
    const { unmount } = render(
      <GoalButton periodType="year" periodStart="2026" periodLabel="2026" />,
    );
    expect(screen.getByRole("button", { name: "Goals" })).toBeInTheDocument();
    unmount();
    mockUseGoalProgress.mockReturnValue({ data: WITH_GOAL, isLoading: false, isError: false });
    render(<GoalButton periodType="year" periodStart="2026" periodLabel="2026" />);
    expect(screen.getByRole("button", { name: "Goals" })).toBeInTheDocument();
  });

  it("the popup holds both the running/distance goal and the bouldering goals", () => {
    mockUseGoalProgress.mockReturnValue({ data: NO_GOAL, isLoading: false, isError: false });
    render(<GoalButton periodType="month" periodStart="2026-10" periodLabel="October 2026" />);
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));
    expect(screen.getByRole("heading", { name: /Running/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bouldering" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: /October 2026 goals/ })).toBeInTheDocument();
  });

  it("a week gets the running/distance goal too, requested for that exact week", () => {
    mockUseGoalProgress.mockReturnValue({ data: NO_GOAL, isLoading: false, isError: false });
    render(
      <GoalButton periodType="week" periodStart="2026-09-27" periodLabel="Week of 2026-09-27" />,
    );
    expect(mockUseGoalProgress).toHaveBeenCalledWith("week", "2026-09-27");
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));
    expect(screen.getByRole("heading", { name: /Running/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bouldering" })).toBeInTheDocument();
    expect(screen.getByText("Target distance (km)")).toBeInTheDocument();
  });

  it("the graph never renders on the page itself, only after opening the popup", () => {
    mockUseGoalProgress.mockReturnValue({ data: WITH_GOAL, isLoading: false, isError: false });
    const { container } = render(
      <GoalButton periodType="year" periodStart="2026" periodLabel="2026" />,
    );
    expect(container.querySelector(".goal-progress")).not.toBeInTheDocument();
  });

  it("clicking the button opens a popup with the form when no goal is set", () => {
    mockUseGoalProgress.mockReturnValue({ data: NO_GOAL, isLoading: false, isError: false });
    render(<GoalButton periodType="year" periodStart="2026" periodLabel="2026" />);

    fireEvent.click(screen.getByRole("button", { name: "Goals" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Target distance (km)")).toBeInTheDocument();
  });

  it("clicking the button opens a popup with the chart and summary when a goal exists", () => {
    mockUseGoalProgress.mockReturnValue({ data: WITH_GOAL, isLoading: false, isError: false });
    render(<GoalButton periodType="year" periodStart="2026" periodLabel="2026" />);

    fireEvent.click(screen.getByRole("button", { name: "Goals" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("1287.0 km")).toBeInTheDocument();
    expect(screen.getByText("+32.4 km")).toBeInTheDocument();
    expect(screen.getByText("ahead of pace")).toBeInTheDocument();
  });

  it("Edit goal reveals the form pre-filled with the existing target", () => {
    mockUseGoalProgress.mockReturnValue({ data: WITH_GOAL, isLoading: false, isError: false });
    render(<GoalButton periodType="year" periodStart="2026" periodLabel="2026" />);
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));

    fireEvent.click(screen.getByText("Edit goal"));

    const input = screen.getByLabelText("Target distance (km)") as HTMLInputElement;
    expect(input.value).toBe("2000");
  });

  it("closing the popup (Escape) removes the dialog from the document", () => {
    mockUseGoalProgress.mockReturnValue({ data: NO_GOAL, isLoading: false, isError: false });
    render(<GoalButton periodType="year" periodStart="2026" periodLabel="2026" />);
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Delete goal calls the delete mutation with the current goal", () => {
    mockUseGoalProgress.mockReturnValue({ data: WITH_GOAL, isLoading: false, isError: false });
    render(<GoalButton periodType="year" periodStart="2026" periodLabel="2026" />);
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));

    fireEvent.click(screen.getByText("Delete goal"));

    expect(mockDeleteGoalMutate).toHaveBeenCalledWith(WITH_GOAL.goal);
  });

  it("a new weekly goal can be repeated for several weeks; a month goal has no repeat option", () => {
    mockUseGoalProgress.mockReturnValue({ data: NO_GOAL, isLoading: false, isError: false });
    const { unmount } = render(
      <GoalButton periodType="week" periodStart="2026-09-28" periodLabel="Week" />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));
    fireEvent.change(screen.getByLabelText(/Target distance/), { target: { value: "40" } });
    fireEvent.change(screen.getByLabelText(/Repeat for/), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Set goal" }));
    expect(mockRepeatGoalMutate).toHaveBeenCalledWith(
      {
        period_type: "week",
        period_start: "2026-09-28",
        sport: "running",
        target_distance_m: 40000,
        weeks: 6,
      },
      expect.anything(),
    );
    expect(mockSetGoalMutate).not.toHaveBeenCalled();
    unmount();

    render(<GoalButton periodType="month" periodStart="2026-10" periodLabel="Oct" />);
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));
    expect(screen.queryByLabelText(/Repeat for/)).not.toBeInTheDocument();
  });

  it("repeating for 1 week is just a normal single goal", () => {
    mockSetGoalMutate.mockClear();
    mockRepeatGoalMutate.mockClear();
    mockUseGoalProgress.mockReturnValue({ data: NO_GOAL, isLoading: false, isError: false });
    render(<GoalButton periodType="week" periodStart="2026-09-28" periodLabel="Week" />);
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));
    fireEvent.change(screen.getByLabelText(/Target distance/), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "Set goal" }));
    expect(mockSetGoalMutate).toHaveBeenCalledTimes(1);
    expect(mockRepeatGoalMutate).not.toHaveBeenCalled();
  });
});
