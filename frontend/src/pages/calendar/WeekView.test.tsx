// Focused on this session's own feature: the day-by-day column strip (WeekDayColumn) that
// replaced the old stacked day-group list, plus the per-day planned-workout/race indicators it
// hosts. Every other query hook WeekView needs is stubbed to an empty/loading-free state so
// those sections render nothing, keeping this test's mock surface bounded to what's actually
// under test -- same approach MonthView.test.tsx already established for its own equally
// hook-heavy page.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ActivitySummary, PlannedWorkoutOut } from "../../api/types";
import { WeekView } from "./WeekView";

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

afterEach(() => {
  window.history.pushState({}, "", "/");
});

function activity(overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    id: "act1",
    start_time_utc: "2026-09-01T13:00:00Z",
    utc_offset_s: 0,
    local_date: "2026-09-01",
    sport: "running",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m: 5000,
    elevation_gain_m: null,
    max_altitude_m: null,
    calories: null,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    vdot: null,
    workout_name: null,
    primary_source: "test",
    stream_available: false,
    climb_route_count: null,
    climb_max_completed_grade: null,
    climb_time_s: null,
    ...overrides,
  };
}

const mockUsePlannedWorkoutsForDate = vi.fn();
mockUsePlannedWorkoutsForDate.mockReturnValue({ data: [], isLoading: false, isError: false });
// A default (see MonthView.test.tsx's own identical comment) -- mockReturnValueOnce overrides
// it for exactly one call without leaking into every other test in this file.
const mockUsePlannedRacesForDate = vi.fn();
mockUsePlannedRacesForDate.mockReturnValue({ data: [], isLoading: false, isError: false });
const mockUseActivities = vi.fn();
mockUseActivities.mockReturnValue({ data: { items: [] }, isLoading: false, isError: false });
const EMPTY_QUERY = { data: undefined, isLoading: false, isError: false };
const EMPTY_DAYS_QUERY = { data: { days: [] }, isLoading: false, isError: false };
const EMPTY_PERIODS_QUERY = { data: { periods: [] }, isLoading: false, isError: false };

vi.mock("../../api/queries", () => ({
  useCalendar: () => EMPTY_DAYS_QUERY,
  useCalendarWeeks: () => EMPTY_PERIODS_QUERY,
  useActivities: (...args: unknown[]) => mockUseActivities(...args),
  useAllActivities: () => ({ data: undefined }),
  useFitness: () => ({ data: undefined }),
  useHealthDashboard: () => EMPTY_QUERY,
  useSleep: () => EMPTY_QUERY,
  useClimbingSummary: () => EMPTY_QUERY,
  useActivityYears: () => EMPTY_QUERY,
  useActivityLocation: () => ({ data: undefined }),
  usePlannedWorkoutsForDate: (...args: unknown[]) => mockUsePlannedWorkoutsForDate(...args),
  usePlannedRacesForDate: (...args: unknown[]) => mockUsePlannedRacesForDate(...args),
}));

const RUNNING_WORKOUT: PlannedWorkoutOut = {
  available: true,
  id: 1,
  local_date: "2026-09-01",
  sport: "running",
  name: "Tempo run",
  source_text: "Warmup 10m",
  scheduled_time: "06:00",
  comment: null,
  estimated_duration_s: 600,
  steps: [],
  parse_errors: [],
  push_status: "draft",
  push_error: null,
  garmin_workout_id: null,
  garmin_scheduled_at: null,
  completed_at: null,
  estimated_distance_m: 2000,
  estimated_load: 30,
  segments: [{ duration_s: 600, zone: 2, intensity_factor: 0.9 }],
};

describe("WeekView planned-workout indicator", () => {
  it("shows nothing for a day with no scheduled workout", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<WeekView date="2026-09-01" />);
    expect(screen.queryByText(/Tempo run/)).not.toBeInTheDocument();
  });

  it("fetches each day of the week individually via usePlannedWorkoutsForDate", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<WeekView date="2026-09-01" />);
    // One call per day of the (Monday-start) week -- 2026-08-31 (Mon) through 2026-09-06 (Sun).
    const calledDates = mockUsePlannedWorkoutsForDate.mock.calls.map((c) => c[0]);
    for (const d of [
      "2026-08-31",
      "2026-09-01",
      "2026-09-02",
      "2026-09-03",
      "2026-09-04",
      "2026-09-05",
      "2026-09-06",
    ]) {
      expect(calledDates).toContain(d);
    }
  });

  it("shows the workout name, time, and load bar for a day with a scheduled run", () => {
    mockUsePlannedWorkoutsForDate.mockImplementation((date: string) =>
      date === "2026-09-01"
        ? { data: [RUNNING_WORKOUT], isLoading: false, isError: false }
        : { data: [], isLoading: false, isError: false },
    );
    render(<WeekView date="2026-09-01" />);
    expect(screen.getByText(/06:00 Tempo run/)).toBeInTheDocument();
    expect(screen.getByText("Load 30")).toBeInTheDocument();
    expect(document.querySelector(".workout-load-bar__segment")).toBeInTheDocument();
  });
});

describe("WeekView race indicator", () => {
  it("shows a race chip on its own scheduled day", () => {
    mockUsePlannedRacesForDate.mockImplementation((date: string) =>
      date === "2026-09-03"
        ? {
            data: [
              {
                id: 1,
                local_date: "2026-09-03",
                name: "Fall 10K",
                sport: "running",
                distance_m: 10000,
                scheduled_time: "08:00",
                target_duration_s: null,
                days_until: 2,
                predicted_duration_s: null,
              },
            ],
            isLoading: false,
            isError: false,
          }
        : { data: [], isLoading: false, isError: false },
    );
    render(<WeekView date="2026-09-01" />);
    expect(screen.getByText(/08:00 Fall 10K/)).toBeInTheDocument();
    expect(document.querySelector(".month-grid__race .icon")).toBeInTheDocument();
  });
});

describe("WeekView day columns", () => {
  it("renders one column per day of the week", () => {
    render(<WeekView date="2026-09-01" />);
    expect(document.querySelectorAll(".week-columns__day")).toHaveLength(7);
  });

  it("shows that day's recorded activities inside its own column", () => {
    mockUseActivities.mockReturnValue({
      data: { items: [activity({ id: "a1", local_date: "2026-09-01", name: "Morning run" })] },
      isLoading: false,
      isError: false,
    });
    render(<WeekView date="2026-09-01" />);
    expect(screen.getByText("Morning run")).toBeInTheDocument();
  });

  it("navigates to that day's /day/:date view when the column is clicked", () => {
    render(<WeekView date="2026-09-01" />);
    const monday = document.querySelectorAll(".week-columns__day")[0]!;
    fireEvent.click(monday);
    expect(window.location.pathname).toBe("/day/2026-08-31");
  });

  it("navigating to an activity inside a column doesn't also navigate to the day", () => {
    mockUseActivities.mockReturnValue({
      data: { items: [activity({ id: "a1", local_date: "2026-09-01" })] },
      isLoading: false,
      isError: false,
    });
    render(<WeekView date="2026-09-01" />);
    fireEvent.click(screen.getByRole("link", { name: /running/i }));
    expect(window.location.pathname).toBe("/activities/a1");
  });
});
