import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GoalCelebration } from "./GoalCelebration";

let monthDistance: unknown[] = [];

vi.mock("../api/queries", () => ({
  useGoalProgress: (period: string) => ({ data: period === "month" ? monthDistance : [] }),
  useDurationGoals: () => ({ data: [] }),
  useBoulderingGoals: () => ({ data: [] }),
}));
vi.mock("../formatDistance", () => ({
  useDistanceFormat: () => ({ metersToDisplay: (m: number) => m / 1000, unitLabel: "km" }),
}));

function reached(pct: number) {
  return [
    {
      available: true,
      goal: {
        id: 7,
        period_type: "month",
        period_start: "2026-11",
        sport: "running",
        target_distance_m: 100000,
      },
      pct_complete: pct,
    },
  ];
}

beforeEach(() => {
  window.localStorage.clear();
  monthDistance = [];
});

describe("GoalCelebration", () => {
  it("shows nothing while no goal is reached", () => {
    monthDistance = reached(0.8);
    render(<GoalCelebration />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("congratulates once for a reached goal, then stays closed", () => {
    monthDistance = reached(1.1);
    const { unmount } = render(<GoalCelebration />);
    expect(screen.getByRole("dialog", { name: "Congratulations" })).toBeInTheDocument();
    expect(screen.getByText("100 km running")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Thanks" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    unmount();
    render(<GoalCelebration />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
