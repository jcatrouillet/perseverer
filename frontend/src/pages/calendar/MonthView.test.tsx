// Focused on the one thing this session's own feature touches: the month grid's per-day
// "planned workout" indicator (docs/adr/0015-scheduled-workouts.md). Every other query hook
// MonthView (and the GoalButton/PeriodShareButton it renders unconditionally) needs is stubbed
// to an empty/loading-free state so those panels render nothing, keeping this test's mock
// surface bounded to what the indicator itself actually needs.
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { PlannedWorkoutListItemOut } from "../../api/types";
import { MonthView } from "./MonthView";

// DateNavigator (rendered unconditionally by MonthView) uses useIsMobile, which reads
// window.matchMedia -- not implemented by jsdom, so every render needs a stub. No other test
// file in this app has exercised a page that pulls DateNavigator in yet, hence no shared setup
// for this already.
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

const mockUsePlannedWorkoutsList = vi.fn();

const EMPTY_QUERY = { data: undefined, isLoading: false, isError: false };
const EMPTY_PERIODS_QUERY = { data: { periods: [] }, isLoading: false, isError: false };
const EMPTY_DAYS_QUERY = { data: { days: [] }, isLoading: false, isError: false };

vi.mock("../../api/queries", () => ({
  // data: undefined (not {items: []}) so the `data &&` guards around RunningStats/HikeStatsCard
  // skip rendering those cards entirely -- both pull in their own further hook chains
  // (useActivityLocation etc.) that this test has no reason to also stub.
  useActivities: () => EMPTY_QUERY,
  useAllActivities: () => ({ data: undefined }),
  useCalendar: () => EMPTY_DAYS_QUERY,
  useCalendarMonths: () => EMPTY_PERIODS_QUERY,
  useCalendarWeeks: () => EMPTY_PERIODS_QUERY,
  useClimbingSummary: () => EMPTY_QUERY,
  useFitness: () => EMPTY_QUERY,
  useHealthDashboard: () => EMPTY_QUERY,
  useSleep: () => EMPTY_QUERY,
  usePlannedWorkoutsList: (...args: unknown[]) => mockUsePlannedWorkoutsList(...args),
  useGoalProgress: () => ({
    data: {
      available: false,
      goal: null,
      period_end: null,
      daily: [],
      target_per_day_m: null,
      current_distance_m: null,
      target_distance_as_of_today_m: null,
      ahead_behind_m: null,
      pct_complete: null,
    },
    isLoading: false,
    isError: false,
  }),
  useSetGoal: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useDeleteGoal: () => ({ mutate: vi.fn() }),
  useCreatePeriodShare: () => ({ mutate: vi.fn(), isPending: false, data: undefined }),
  useCreateActivityShare: () => ({ mutate: vi.fn(), isPending: false, data: undefined }),
  useActivityYears: () => ({ data: undefined, isLoading: false, isError: false }),
}));

const PLANNED: PlannedWorkoutListItemOut = {
  local_date: "2026-09-15",
  id: 1,
  sport: "running",
  name: "Tempo run",
  push_status: "draft",
};

describe("MonthView day-cell planned-workout indicator", () => {
  it("shows no indicator when nothing is scheduled that month", () => {
    mockUsePlannedWorkoutsList.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<MonthView year={2026} month={9} />);
    expect(screen.queryByText(/Tempo run/)).not.toBeInTheDocument();
  });

  it("shows the workout name on the day it's scheduled", () => {
    mockUsePlannedWorkoutsList.mockReturnValue({
      data: [PLANNED],
      isLoading: false,
      isError: false,
    });
    render(<MonthView year={2026} month={9} />);
    expect(screen.getByText(/Tempo run/)).toBeInTheDocument();
  });

  it("falls back to the sport name when no workout name was given", () => {
    mockUsePlannedWorkoutsList.mockReturnValue({
      data: [{ ...PLANNED, name: null }],
      isLoading: false,
      isError: false,
    });
    render(<MonthView year={2026} month={9} />);
    expect(screen.getByText(/running/)).toBeInTheDocument();
  });
});
