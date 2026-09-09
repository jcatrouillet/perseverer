// Focused on the one thing this session's own feature touches: the per-day planned-workout
// indicator (WeekDayPlannedWorkouts) newly added to WeekView.tsx. Every other query hook
// WeekView needs is stubbed to an empty/loading-free state so those sections render nothing,
// keeping this test's mock surface bounded to what the indicator itself actually needs -- same
// approach MonthView.test.tsx already established for its own equally hook-heavy page.
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { PlannedWorkoutOut } from "../../api/types";
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

const mockUsePlannedWorkoutsForDate = vi.fn();
const EMPTY_QUERY = { data: undefined, isLoading: false, isError: false };
const EMPTY_DAYS_QUERY = { data: { days: [] }, isLoading: false, isError: false };
const EMPTY_PERIODS_QUERY = { data: { periods: [] }, isLoading: false, isError: false };

vi.mock("../../api/queries", () => ({
  useCalendar: () => EMPTY_DAYS_QUERY,
  useCalendarWeeks: () => EMPTY_PERIODS_QUERY,
  useActivities: () => ({ data: { items: [] }, isLoading: false, isError: false }),
  useAllActivities: () => ({ data: undefined }),
  useFitness: () => ({ data: undefined }),
  useHealthDashboard: () => EMPTY_QUERY,
  useSleep: () => EMPTY_QUERY,
  useClimbingSummary: () => EMPTY_QUERY,
  useActivityYears: () => EMPTY_QUERY,
  useActivityLocation: () => ({ data: undefined }),
  usePlannedWorkoutsForDate: (...args: unknown[]) => mockUsePlannedWorkoutsForDate(...args),
}));

const RUNNING_WORKOUT: PlannedWorkoutOut = {
  available: true,
  id: 1,
  local_date: "2026-09-01",
  sport: "running",
  name: "Tempo run",
  source_text: "Warmup 10m",
  scheduled_time: "06:00",
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
