import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { GoalProgressOut } from "../api/types";
import { GoalButton } from "./GoalButton";

const mockUseGoalProgress = vi.fn();
const mockCreateMutate = vi.fn();
const mockUpdateMutate = vi.fn();
const mockDeleteGoalMutate = vi.fn();
const mockRepeatGoalMutate = vi.fn();

vi.mock("../api/queries", () => ({
  useGoalProgress: (...args: unknown[]) => mockUseGoalProgress(...args),
  // The bouldering and time halves of the same popup.
  useBoulderingGoals: () => ({ data: [], isLoading: false, isError: false }),
  useCreateBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useUpdateBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false }),
  useDurationGoals: () => ({ data: [], isLoading: false, isError: false }),
  useCreateDurationGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useUpdateDurationGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteDurationGoal: () => ({ mutate: vi.fn(), isPending: false }),
  useRepeatDurationGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useCreateGoal: () => ({ mutate: mockCreateMutate, isPending: false, error: null }),
  useUpdateGoal: () => ({ mutate: mockUpdateMutate, isPending: false, error: null }),
  useDeleteGoal: () => ({ mutate: mockDeleteGoalMutate, isPending: false }),
  useRepeatGoal: () => ({ mutate: mockRepeatGoalMutate, isPending: false, error: null }),
  useRepeatBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));

function goalProgress(
  id: number,
  sport: string | null,
  target: number,
  current: number,
): GoalProgressOut {
  return {
    available: true,
    goal: {
      id,
      period_type: "year",
      period_start: "2026",
      sport,
      target_distance_m: target,
    },
    period_end: "2026-12-31",
    daily: [
      { local_date: "2026-01-01", cumulative_distance_m: 0 },
      { local_date: "2026-08-17", cumulative_distance_m: current },
    ],
    target_per_day_m: target / 365,
    current_distance_m: current,
    target_distance_as_of_today_m: current - 32_400,
    ahead_behind_m: 32_400,
    pct_complete: current / target,
  };
}

const RUNNING = goalProgress(1, "running", 2_000_000, 1_287_000);
const CYCLING = goalProgress(2, "cycling", 5_000_000, 1_500_000);

function loaded(data: GoalProgressOut[]) {
  mockUseGoalProgress.mockReturnValue({ data, isLoading: false, isError: false });
}

function open(periodType: "week" | "month" | "year" = "year", periodStart = "2026") {
  render(<GoalButton periodType={periodType} periodStart={periodStart} periodLabel="Period" />);
  fireEvent.click(screen.getByRole("button", { name: "Goals" }));
}

describe("GoalButton", () => {
  beforeEach(() => {
    mockCreateMutate.mockClear();
    mockUpdateMutate.mockClear();
    mockDeleteGoalMutate.mockClear();
    mockRepeatGoalMutate.mockClear();
  });

  it("is one button labelled Goals, whether or not a goal is set", () => {
    loaded([]);
    const { unmount } = render(
      <GoalButton periodType="year" periodStart="2026" periodLabel="2026" />,
    );
    expect(screen.getByRole("button", { name: "Goals" })).toBeInTheDocument();
    unmount();
    loaded([RUNNING]);
    render(<GoalButton periodType="year" periodStart="2026" periodLabel="2026" />);
    expect(screen.getByRole("button", { name: "Goals" })).toBeInTheDocument();
  });

  it("the popup holds distance, time and bouldering goals, the distance ones for any sport", () => {
    loaded([]);
    open("month", "2026-10");
    expect(screen.getByRole("dialog", { name: /Period goals/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Distance" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Time" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bouldering" })).toBeInTheDocument();
    // Not titled after one sport: any sport that covers a distance can have a goal.
    expect(screen.queryByRole("heading", { name: /Running/ })).not.toBeInTheDocument();
  });

  it("a week gets distance goals too, requested for that exact week", () => {
    loaded([]);
    open("week", "2026-09-27");
    expect(mockUseGoalProgress).toHaveBeenCalledWith("week", "2026-09-27");
  });

  it("the graph never renders on the page itself, only after opening the popup", () => {
    loaded([RUNNING]);
    const { container } = render(
      <GoalButton periodType="year" periodStart="2026" periodLabel="2026" />,
    );
    expect(container.querySelector(".goal-progress")).not.toBeInTheDocument();
  });

  it("with no goal it says so and offers to add one, with no form open yet", () => {
    loaded([]);
    open();
    expect(screen.getByText(/No distance goal for this period yet/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Target distance (km)")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add a distance goal" }));
    expect(screen.getByLabelText("Target distance (km)")).toBeInTheDocument();
  });

  it("lists every distance goal of the period, one card per sport", () => {
    loaded([CYCLING, RUNNING, goalProgress(3, null, 8_000_000, 2_800_000)]);
    open();
    expect(screen.getByRole("heading", { name: "5000 km cycling" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "2000 km running" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "8000 km all sports" })).toBeInTheDocument();
    expect(screen.getByText("1287.0 km")).toBeInTheDocument();
    expect(screen.getByText("1500.0 km")).toBeInTheDocument();
    expect(screen.getAllByText("ahead of pace")).toHaveLength(3);
  });

  it("adds a goal for another sport while one already exists", () => {
    loaded([RUNNING]);
    open();
    fireEvent.click(screen.getByRole("button", { name: "+ Add a distance goal" }));
    fireEvent.change(screen.getByLabelText("Sport"), { target: { value: "cycling" } });
    fireEvent.change(screen.getByLabelText("Target distance (km)"), { target: { value: "5000" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
    expect(mockCreateMutate).toHaveBeenCalledWith(
      {
        period_type: "year",
        period_start: "2026",
        sport: "cycling",
        target_distance_m: 5_000_000,
      },
      expect.anything(),
    );
    expect(mockUpdateMutate).not.toHaveBeenCalled();
  });

  it("Edit reveals the form pre-filled with that goal's sport and target", () => {
    loaded([RUNNING, CYCLING]);
    open();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]!);
    expect((screen.getByLabelText("Target distance (km)") as HTMLInputElement).value).toBe("5000");
    expect((screen.getByLabelText("Sport") as HTMLSelectElement).value).toBe("cycling");
    fireEvent.change(screen.getByLabelText("Target distance (km)"), { target: { value: "6000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockUpdateMutate).toHaveBeenCalledWith(
      {
        id: 2,
        period_type: "year",
        period_start: "2026",
        sport: "cycling",
        target_distance_m: 6_000_000,
      },
      expect.anything(),
    );
  });

  it("closing the popup (Escape) removes the dialog from the document", () => {
    loaded([]);
    open();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Delete removes that one goal only", () => {
    loaded([RUNNING, CYCLING]);
    open();
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    expect(mockDeleteGoalMutate).toHaveBeenCalledTimes(1);
    expect(mockDeleteGoalMutate).toHaveBeenCalledWith(RUNNING.goal);
  });

  it("a new weekly goal can be repeated for several weeks; a month goal has no repeat option", () => {
    loaded([]);
    const { unmount } = render(
      <GoalButton periodType="week" periodStart="2026-09-28" periodLabel="Week" />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Add a distance goal" }));
    fireEvent.change(screen.getByLabelText(/Target distance/), { target: { value: "40" } });
    fireEvent.change(screen.getByLabelText(/Repeat for/), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
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
    expect(mockCreateMutate).not.toHaveBeenCalled();
    unmount();

    render(<GoalButton periodType="month" periodStart="2026-10" periodLabel="Oct" />);
    fireEvent.click(screen.getByRole("button", { name: "Goals" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Add a distance goal" }));
    expect(screen.queryByLabelText(/Repeat for/)).not.toBeInTheDocument();
  });

  it("repeating for 1 week is just a normal single goal", () => {
    loaded([]);
    open("week", "2026-09-28");
    fireEvent.click(screen.getByRole("button", { name: "+ Add a distance goal" }));
    fireEvent.change(screen.getByLabelText(/Target distance/), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
    expect(mockCreateMutate).toHaveBeenCalledTimes(1);
    expect(mockRepeatGoalMutate).not.toHaveBeenCalled();
  });

  it("only offers sports that cover a distance", () => {
    loaded([]);
    open();
    fireEvent.click(screen.getByRole("button", { name: "+ Add a distance goal" }));
    const select = screen.getByLabelText("Sport") as HTMLSelectElement;
    const offered = [...select.options].map((o) => o.value);
    expect(offered).toEqual([
      "",
      "running",
      "cycling",
      "rowing",
      "walking",
      "hiking",
      "snowshoeing",
      "alpine_skiing",
    ]);
    for (const noDistance of [
      "strength_training",
      "fitness_equipment",
      "hiit",
      "yoga",
      "breathing",
      "rock_climbing",
      "racket",
    ]) {
      expect(offered).not.toContain(noDistance);
    }
  });

  it("keeps an already-saved goal's sport selectable even if it is no longer offered", () => {
    loaded([goalProgress(9, "yoga", 1_000_000, 100_000)]);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect((screen.getByLabelText("Sport") as HTMLSelectElement).value).toBe("yoga");
  });
});
