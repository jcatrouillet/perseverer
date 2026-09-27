// Focused on the one thing this session's own feature touches: the month grid's per-day
// "planned workout" indicator (docs/adr/0015-scheduled-workouts.md). Every other query hook
// MonthView (and the GoalButton/PeriodShareButton it renders unconditionally) needs is stubbed
// to an empty/loading-free state so those panels render nothing, keeping this test's mock
// surface bounded to what the indicator itself actually needs.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { DayRollupOut, PlannedWorkoutListItemOut } from "../../api/types";
import { MonthView } from "./MonthView";

function dayRollup(overrides: Partial<DayRollupOut> = {}): DayRollupOut {
  return {
    local_date: "2026-09-01",
    activity_count: 0,
    activity_duration_s: null,
    activity_moving_duration_s: null,
    activity_distance_m: null,
    activity_elevation_gain_m: null,
    activity_calories: null,
    sleep_total_s: null,
    sleep_score: null,
    health_metrics: [],
    ...overrides,
  };
}

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
// A default (not per-test, unlike mockUsePlannedWorkoutsList above) since most tests in this
// file don't care about races at all -- mockReturnValueOnce below overrides it for exactly one
// call without permanently clobbering this default for every test after it (mocks aren't
// auto-reset between tests in this file).
const mockUsePlannedRacesForRange = vi.fn();
mockUsePlannedRacesForRange.mockReturnValue({ data: [], isLoading: false, isError: false });

const EMPTY_QUERY = { data: undefined, isLoading: false, isError: false };
const EMPTY_PERIODS_QUERY = { data: { periods: [] }, isLoading: false, isError: false };
const EMPTY_DAYS_QUERY = { data: { days: [] }, isLoading: false, isError: false };
const mockUseCalendar = vi.fn();
mockUseCalendar.mockReturnValue(EMPTY_DAYS_QUERY);

vi.mock("../../api/queries", () => ({
  // data: undefined (not {items: []}) so the `data &&` guards around RunningStats/HikeStatsCard
  // skip rendering those cards entirely -- both pull in their own further hook chains
  // (useActivityLocation etc.) that this test has no reason to also stub.
  useActivities: () => EMPTY_QUERY,
  useAllActivities: () => ({ data: undefined }),
  useCalendar: (...args: unknown[]) => mockUseCalendar(...args),
  useCalendarMonths: () => EMPTY_PERIODS_QUERY,
  useClimbingSummary: () => EMPTY_QUERY,
  useFitness: () => EMPTY_QUERY,
  useHealthDashboard: () => EMPTY_QUERY,
  useSleep: () => EMPTY_QUERY,
  usePlannedWorkoutsList: (...args: unknown[]) => mockUsePlannedWorkoutsList(...args),
  usePlannedRacesForRange: (...args: unknown[]) => mockUsePlannedRacesForRange(...args),
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
  // The bouldering half of the Goals popup.
  useBoulderingGoals: () => ({ data: [], isLoading: false, isError: false }),
  useCreateBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useUpdateBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteGoal: () => ({ mutate: vi.fn() }),
  useCreatePeriodShare: () => ({ mutate: vi.fn(), isPending: false, data: undefined }),
  useCreateActivityShare: () => ({ mutate: vi.fn(), isPending: false, data: undefined }),
  useActivityYears: () => ({ data: undefined, isLoading: false, isError: false }),
  // Needed once a day is expanded -- ScheduleWorkoutForm and NotesPanel both render inside the
  // expanded-date card (see the "expandedDate resets on month change" test below).
  usePlannedWorkoutsForDate: () => ({ data: [], isLoading: false, isError: false }),
  useCreatePlannedWorkout: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdatePlannedWorkout: () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePlannedWorkout: () => ({ mutate: vi.fn(), isPending: false }),
  usePushPlannedWorkout: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateRecurringPlannedWorkouts: () => ({ mutate: vi.fn(), isPending: false, data: undefined }),
  usePlannedRacesForDate: () => ({ data: [], isLoading: false, isError: false }),
  useCreatePlannedRace: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdatePlannedRace: () => ({ mutate: vi.fn(), isPending: false }),
  useDeletePlannedRace: () => ({ mutate: vi.fn(), isPending: false }),
  useNotes: () => ({ data: [], isLoading: false, isError: false }),
  useCreateNote: () => ({ mutate: vi.fn(), isPending: false }),
}));

const PLANNED: PlannedWorkoutListItemOut = {
  local_date: "2026-09-15",
  id: 1,
  sport: "running",
  name: "Tempo run",
  scheduled_time: null,
  push_status: "draft",
  completed_at: null,
  matched_activity_id: null,
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

  it("shows the scheduled time alongside the workout name when set", () => {
    mockUsePlannedWorkoutsList.mockReturnValue({
      data: [{ ...PLANNED, scheduled_time: "18:30" }],
      isLoading: false,
      isError: false,
    });
    render(<MonthView year={2026} month={9} />);
    expect(screen.getByText(/18:30 Tempo run/)).toBeInTheDocument();
  });

  it("shows a sport icon alongside the workout indicator", () => {
    mockUsePlannedWorkoutsList.mockReturnValue({
      data: [PLANNED],
      isLoading: false,
      isError: false,
    });
    render(<MonthView year={2026} month={9} />);
    expect(document.querySelector(".month-grid__planned .icon")).toBeInTheDocument();
  });
});

describe("MonthView day-cell race indicator", () => {
  it("shows a race chip on the day it's scheduled, alongside any planned workout", () => {
    mockUsePlannedWorkoutsList.mockReturnValue({
      data: [PLANNED],
      isLoading: false,
      isError: false,
    });
    mockUsePlannedRacesForRange.mockReturnValueOnce({
      data: [
        {
          id: 1,
          local_date: "2026-09-10",
          name: "Fall 10K",
          sport: "running",
          distance_m: 10000,
          scheduled_time: null,
          target_duration_s: null,
          days_until: 5,
          predicted_duration_s: null,
        },
      ],
      isLoading: false,
      isError: false,
    });
    render(<MonthView year={2026} month={9} />);
    expect(screen.getByText("Fall 10K")).toBeInTheDocument();
    expect(document.querySelector(".month-grid__race .icon")).toBeInTheDocument();
  });
});

describe("MonthView expanded-date card", () => {
  it("resets to collapsed when the month changes, rather than keeping the old date expanded", () => {
    // wouter re-renders this same MonthView instance with new year/month props when navigating
    // month-to-month (DateNavigator) -- rerender() reproduces exactly that, unlike a fresh
    // render() per month, which would never have caught this (reported) bug.
    mockUsePlannedWorkoutsList.mockReturnValue({ data: [], isLoading: false, isError: false });
    const { rerender } = render(<MonthView year={2026} month={9} />);

    const dayButtons = document.querySelectorAll(".month-grid__day-btn");
    const day15 = [...dayButtons].find((b) => b.textContent === "15");
    expect(day15).toBeTruthy();
    fireEvent.click(day15!);
    expect(screen.getByText("2026-09-15")).toBeInTheDocument();

    rerender(<MonthView year={2026} month={10} />);

    expect(screen.queryByText("2026-09-15")).not.toBeInTheDocument();
  });
});

describe("MonthView per-row week total", () => {
  afterEach(() => {
    mockUseCalendar.mockReturnValue(EMPTY_DAYS_QUERY);
  });

  it("sums a row's own week total from the padded grid's per-day rollups, including an adjacent-month day", () => {
    // September 2026's Monday-start grid begins Mon 2026-08-31 (an adjacent-month day) --
    // confirming the padded gridStart/gridEnd range (not just the month's own start/end) is
    // what's actually fetched and summed for that first row's own week total.
    mockUseCalendar.mockReturnValue({
      data: {
        days: [
          dayRollup({
            local_date: "2026-08-31",
            activity_count: 1,
            activity_distance_m: 4000,
          }),
          dayRollup({
            local_date: "2026-09-01",
            activity_count: 1,
            activity_distance_m: 6000,
          }),
        ],
      },
      isLoading: false,
      isError: false,
    });
    render(<MonthView year={2026} month={9} />);
    const weekLink = screen.getByRole("link", { name: /2 act.*10\.0 km/ });
    expect(weekLink).toHaveAttribute("href", "/calendar/week/2026-08-31");
  });
});
