// Focused on this session's own feature: the day-by-day column strip (WeekDayColumn) that
// replaced the old stacked day-group list, plus the per-day planned-workout/race indicators it
// hosts. Every other query hook WeekView needs is stubbed to an empty/loading-free state so
// those sections render nothing, keeping this test's mock surface bounded to what's actually
// under test -- same approach MonthView.test.tsx already established for its own equally
// hook-heavy page.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type {
  ActivitySummary,
  DayRollupOut,
  PlannedWorkoutListItemOut,
  PlannedWorkoutOut,
} from "../../api/types";
import { PersonalizeContext } from "../../PersonalizeContext";
import { WeekView } from "./WeekView";

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
const mockUseNotes = vi.fn();
mockUseNotes.mockReturnValue({ data: [], isLoading: false, isError: false });
const mockUseCreateNote = vi.fn(() => ({ mutate: vi.fn(), isPending: false }));
const mockUseWeatherForecast = vi.fn();
mockUseWeatherForecast.mockReturnValue({ data: undefined, isLoading: false, isError: false });
const mockUsePlannedWorkoutsList = vi.fn();
mockUsePlannedWorkoutsList.mockReturnValue({ data: [], isLoading: false, isError: false });
const mockUseHealthDashboard = vi.fn();
const EMPTY_QUERY = { data: undefined, isLoading: false, isError: false };
mockUseHealthDashboard.mockReturnValue(EMPTY_QUERY);
const EMPTY_DAYS_QUERY = { data: { days: [] }, isLoading: false, isError: false };
const mockUseCalendar = vi.fn();
mockUseCalendar.mockReturnValue(EMPTY_DAYS_QUERY);

vi.mock("../../api/queries", () => ({
  useCalendar: (...args: unknown[]) => mockUseCalendar(...args),
  useActivities: (...args: unknown[]) => mockUseActivities(...args),
  useAllActivities: () => ({ data: undefined }),
  useFitness: () => ({ data: undefined }),
  useHealthDashboard: (...args: unknown[]) => mockUseHealthDashboard(...args),
  useSleep: () => EMPTY_QUERY,
  useClimbingSummary: () => EMPTY_QUERY,
  useActivityYears: () => EMPTY_QUERY,
  useActivityLocation: () => ({ data: undefined }),
  usePlannedWorkoutsForDate: (...args: unknown[]) => mockUsePlannedWorkoutsForDate(...args),
  usePlannedRacesForDate: (...args: unknown[]) => mockUsePlannedRacesForDate(...args),
  usePlannedWorkoutsList: (...args: unknown[]) => mockUsePlannedWorkoutsList(...args),
  useNotes: (...args: unknown[]) => mockUseNotes(...args),
  useCreateNote: () => mockUseCreateNote(),
  useWeatherForecast: () => mockUseWeatherForecast(),
  // The single Goals header button (a week only has bouldering goals; the distance query is skipped).
  useGoalProgress: () => ({ data: undefined, isLoading: false, isError: false }),
  useSetGoal: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useDeleteGoal: () => ({ mutate: vi.fn() }),
  useRepeatGoal: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useRepeatBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useBoulderingGoals: () => ({ data: [], isLoading: false, isError: false }),
  useCreateBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useUpdateBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteBoulderingGoal: () => ({ mutate: vi.fn(), isPending: false }),
  useDurationGoals: () => ({ data: [], isLoading: false, isError: false }),
  useCreateDurationGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useUpdateDurationGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteDurationGoal: () => ({ mutate: vi.fn(), isPending: false }),
  useRepeatDurationGoal: () => ({ mutate: vi.fn(), isPending: false, error: null }),
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
  matched_activity_id: null,
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

describe("WeekView weather forecast", () => {
  it("shows nothing when the athlete has no home location set", () => {
    mockUseWeatherForecast.mockReturnValue({
      data: { available: false, days: [] },
      isLoading: false,
      isError: false,
    });
    render(<WeekView date="2026-09-01" />);
    expect(document.querySelector(".week-columns__forecast")).not.toBeInTheDocument();
  });

  it("shows an icon and min/max temperature under a day's date for a day within the forecast", () => {
    mockUseWeatherForecast.mockReturnValue({
      data: {
        available: true,
        days: [
          {
            local_date: "2026-09-01",
            weather_code: 3,
            temperature_min_c: 12.4,
            temperature_max_c: 21.6,
          },
        ],
      },
      isLoading: false,
      isError: false,
    });
    render(<WeekView date="2026-09-01" />);
    const forecastRow = document.querySelector(".week-columns__forecast");
    expect(forecastRow).toBeInTheDocument();
    expect(forecastRow).toHaveTextContent("12–22°");
    expect(forecastRow!.querySelector(".icon")).toBeInTheDocument();
    // Sits right after the header (weekday/date), before the planned-workout/activity sections.
    const day = document.querySelectorAll(".week-columns__day")[1]!; // Tuesday = 2026-09-01
    const header = day.querySelector(".week-columns__header")!;
    expect(
      header.compareDocumentPosition(forecastRow!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows no forecast row for a day outside the returned forecast range", () => {
    mockUseWeatherForecast.mockReturnValue({
      data: {
        available: true,
        days: [
          {
            local_date: "2026-09-01",
            weather_code: 0,
            temperature_min_c: 10.0,
            temperature_max_c: 20.0,
          },
        ],
      },
      isLoading: false,
      isError: false,
    });
    render(<WeekView date="2026-09-01" />);
    expect(document.querySelectorAll(".week-columns__forecast")).toHaveLength(1);
  });
});

describe("WeekView steps", () => {
  afterEach(() => {
    // mockReturnValue (not -Once) persists across tests in this file's own convention -- reset
    // explicitly so a later describe block doesn't inherit this block's own health data.
    mockUseHealthDashboard.mockReturnValue(EMPTY_QUERY);
  });

  it("shows nothing when there's no health data yet", () => {
    mockUseHealthDashboard.mockReturnValue(EMPTY_QUERY);
    render(<WeekView date="2026-09-01" />);
    expect(document.querySelector(".week-columns__steps")).not.toBeInTheDocument();
  });

  it("shows the steps icon and count for a day with a real reading", () => {
    mockUseHealthDashboard.mockReturnValue({
      data: {
        metrics: [
          {
            logical_metric: "steps",
            last_observed: "2026-09-01",
            daily: [
              {
                local_date: "2026-09-01",
                value_sum: 9432,
                value_avg: null,
                value_min: null,
                value_max: null,
                value_last: null,
                n_observations: 1,
                source_metric_key: "garmin.daily_summary.totalSteps",
              },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
    });

    render(<WeekView date="2026-09-01" />);

    const stepsRow = document.querySelector(".week-columns__steps");
    expect(stepsRow).toBeInTheDocument();
    expect(stepsRow).toHaveTextContent("9,432");
    expect(stepsRow!.querySelector(".icon")).toBeInTheDocument();
    // Only the one day with a real reading gets a row -- never a fabricated 0 for the rest.
    expect(document.querySelectorAll(".week-columns__steps")).toHaveLength(1);
  });
});

describe("WeekView compliance", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  function planned(overrides: Partial<PlannedWorkoutListItemOut> = {}): PlannedWorkoutListItemOut {
    return {
      local_date: "2026-09-01",
      id: 1,
      sport: "running",
      name: null,
      scheduled_time: null,
      push_status: "draft",
      completed_at: null,
      matched_activity_id: null,
      ...overrides,
    };
  }

  it("shows no Compliance section when nothing was scheduled this week", () => {
    mockUsePlannedWorkoutsList.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(<WeekView date="2026-09-01" />);
    expect(screen.queryByRole("heading", { name: "Compliance" })).not.toBeInTheDocument();
  });

  it("shows a per-sport compliance percentage, excluding workouts later than today", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-03T12:00:00"));
    mockUsePlannedWorkoutsList.mockReturnValue({
      data: [
        planned({ id: 1, local_date: "2026-09-01", completed_at: "2026-09-01T08:00:00" }),
        planned({ id: 2, local_date: "2026-09-02", completed_at: null }),
        // Later than the faked "today" (Sept 3) -- must not count toward running's totals.
        planned({ id: 3, local_date: "2026-09-05", completed_at: null }),
        planned({
          id: 4,
          local_date: "2026-09-02",
          sport: "yoga",
          completed_at: "2026-09-02T08:00:00",
        }),
      ],
      isLoading: false,
      isError: false,
    });

    render(<WeekView date="2026-09-01" />);

    expect(screen.getByRole("heading", { name: "Compliance" })).toBeInTheDocument();
    expect(screen.getByText("Running compliance")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 done")).toBeInTheDocument();
    expect(screen.getByText("Yoga compliance")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("1 of 1 done")).toBeInTheDocument();
  });

  it("omits the Compliance section entirely for a fully future week", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-01T12:00:00"));
    mockUsePlannedWorkoutsList.mockReturnValue({
      data: [planned({ local_date: "2026-09-01" })],
      isLoading: false,
      isError: false,
    });

    render(<WeekView date="2026-09-01" />);
    expect(screen.queryByRole("heading", { name: "Compliance" })).not.toBeInTheDocument();
  });

  it("counts a workout as done when a matching recorded activity was synced in, with no manual complete", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-01T12:00:00"));
    mockUsePlannedWorkoutsList.mockReturnValue({
      data: [
        planned({ id: 1, local_date: "2026-09-01", sport: "yoga", matched_activity_id: "a1" }),
      ],
      isLoading: false,
      isError: false,
    });

    render(<WeekView date="2026-09-01" />);

    expect(screen.getByText("Yoga compliance")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("1 of 1 done")).toBeInTheDocument();
  });
});

describe("WeekView notes", () => {
  it("shows a week-level Notes section keyed on the week's Monday, before the day columns", () => {
    render(<WeekView date="2026-09-01" />);
    const calledWith = mockUseNotes.mock.calls.map((c) => c[0] + ":" + c[1]);
    expect(calledWith).toContain("week:2026-08-31");

    const heading = screen.getByRole("heading", { name: "Notes" });
    const columns = document.querySelector(".week-columns");
    expect(columns).not.toBeNull();
    // Notes section precedes the day-column strip in document order.
    expect(
      heading.compareDocumentPosition(columns!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("still shows the Notes section for a week with no recorded activities at all", () => {
    // Every mocked hook in this file already defaults to empty data -- this is really just
    // confirming the section isn't gated behind weekTotal/activity data existing, unlike the
    // "Week stats" card below it.
    render(<WeekView date="2026-09-14" />);
    expect(screen.getByRole("heading", { name: "Notes" })).toBeInTheDocument();
  });
});

describe("WeekView week stats", () => {
  afterEach(() => {
    mockUseCalendar.mockReturnValue(EMPTY_DAYS_QUERY);
  });

  it("sums Week stats from the fetched per-day rollups, not a Monday-keyed period total", () => {
    // 2026-09-01 (Tue) falls in the week of Mon 2026-08-31 - Sun 2026-09-06.
    mockUseCalendar.mockReturnValue({
      data: {
        days: [
          dayRollup({
            local_date: "2026-08-31",
            activity_count: 1,
            activity_distance_m: 5000,
            activity_moving_duration_s: 1500,
            activity_elevation_gain_m: 40,
          }),
          dayRollup({
            local_date: "2026-09-02",
            activity_count: 1,
            activity_distance_m: 8000,
            activity_moving_duration_s: 2400,
            activity_elevation_gain_m: null,
          }),
        ],
      },
      isLoading: false,
      isError: false,
    });
    render(<WeekView date="2026-09-01" />);
    const statGrid = document.querySelector(".stat-grid")!;
    expect(statGrid).toHaveTextContent("13.0"); // (5000+8000)/1000 km
    expect(statGrid).toHaveTextContent("2"); // 2 active days
    // One of the two contributing days has a real elevation reading -- the tile shows a real
    // (partial) sum, not hidden the way it would be if neither day recorded any elevation.
    expect(statGrid).toHaveTextContent("40"); // total elevation gain (only one day reported any)
  });

  it("hides the elevation stat tile when no day in the week recorded any elevation channel", () => {
    mockUseCalendar.mockReturnValue({
      data: {
        days: [
          dayRollup({
            local_date: "2026-08-31",
            activity_count: 1,
            activity_distance_m: 5000,
            activity_moving_duration_s: 1500,
            activity_elevation_gain_m: null,
          }),
        ],
      },
      isLoading: false,
      isError: false,
    });
    render(<WeekView date="2026-09-01" />);
    expect(screen.queryByText("Total elevation")).not.toBeInTheDocument();
  });

  it("shifts the visible week and its own totals to Sunday-Saturday when weekStartDay is sunday", () => {
    // 2026-09-01 (Tue) belongs to the Sunday-starting week 2026-08-30 - 2026-09-05 instead.
    mockUseCalendar.mockReturnValue({
      data: {
        days: [
          dayRollup({
            local_date: "2026-08-30",
            activity_count: 1,
            activity_distance_m: 3000,
            activity_moving_duration_s: 900,
          }),
        ],
      },
      isLoading: false,
      isError: false,
    });
    render(
      <PersonalizeContext.Provider
        value={{
          week_start_day: "sunday",
          time_format: "24h",
          default_view: "week",
          unit_preference: "metric",
        }}
      >
        <WeekView date="2026-09-01" />
      </PersonalizeContext.Provider>,
    );
    expect(
      screen.getByRole("heading", { name: "Week of 2026-08-30 – 2026-09-05" }),
    ).toBeInTheDocument();
    const statGrid = document.querySelector(".stat-grid")!;
    expect(statGrid).toHaveTextContent("3.0"); // 3000m -> 3.0 km, only countable once the range
    // actually includes 2026-08-30 (a Sunday, outside the default Monday-start range).
  });
});
