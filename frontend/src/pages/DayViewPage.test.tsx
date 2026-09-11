// Regression test for a real gap: the "Schedule a workout" affordance was originally wired only
// into MonthView.tsx's expanded-day card, not into DayViewPage.tsx -- the page /day/:date
// actually lands on (reached from DateNavigator's own day picker, RunningStats' heatmap cells,
// etc.). See docs/adr/0015-scheduled-workouts.md.
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { DayViewPage } from "./DayViewPage";

// DateNavigator (rendered unconditionally) uses useIsMobile, which reads window.matchMedia --
// not implemented by jsdom. Same stub MonthView.test.tsx needs for the same reason.
beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
});

const mockUsePlannedWorkoutsForDate = vi.fn();
const EMPTY_QUERY = { data: undefined, isLoading: false, isError: false };

vi.mock("../api/queries", () => ({
  useActivities: () => ({ data: { items: [] }, isLoading: false, isError: false }),
  useActivityRoutes: () => ({ data: [] }),
  useAllActivities: () => ({ data: undefined }),
  useCalendar: () => ({ data: { days: [] }, isLoading: false, isError: false }),
  useFitness: () => EMPTY_QUERY,
  useHealthDashboard: () => EMPTY_QUERY,
  useHealthObservations: () => ({ data: { items: [] }, isLoading: false, isError: false }),
  useHealthStream: () => ({ data: undefined }),
  useSleep: () => EMPTY_QUERY,
  useActivityYears: () => EMPTY_QUERY,
  useNotes: () => ({ data: [], isLoading: false, isError: false }),
  useCreateNote: () => ({ mutate: vi.fn(), isPending: false }),
  usePlannedWorkoutsForDate: (...args: unknown[]) => mockUsePlannedWorkoutsForDate(...args),
  useCreatePlannedWorkout: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdatePlannedWorkout: () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePlannedWorkout: () => ({ mutate: vi.fn(), isPending: false }),
  usePushPlannedWorkout: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateRecurringPlannedWorkouts: () => ({ mutate: vi.fn(), isPending: false, data: undefined }),
  usePlannedRacesForDate: () => ({ data: [], isLoading: false, isError: false }),
  useCreatePlannedRace: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdatePlannedRace: () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePlannedRace: () => ({ mutate: vi.fn(), isPending: false }),
}));

describe("DayViewPage", () => {
  it("shows a 'Planned workout' section with a Schedule a workout button", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<DayViewPage date="2026-09-10" />);

    expect(screen.getByText("Planned workout")).toBeInTheDocument();
    expect(screen.getByText("Schedule a workout")).toBeInTheDocument();
  });

  it("passes the page's own date through to the schedule form", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<DayViewPage date="2026-09-10" />);

    expect(mockUsePlannedWorkoutsForDate).toHaveBeenCalledWith("2026-09-10");
  });
});
