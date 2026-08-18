import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "../api/types";
import { WeekRunningStats } from "./WeekRunningStats";

function activity(local_date: string, overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    id: local_date,
    start_time_utc: `${local_date}T12:00:00Z`,
    utc_offset_s: 0,
    local_date,
    sport: "running",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m: 5000,
    elevation_gain_m: null,
    calories: 400,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: 80,
    vdot: null,
    workout_name: null,
    primary_source: "test",
    stream_available: false,
    ...overrides,
  };
}

describe("WeekRunningStats", () => {
  it("renders nothing when there are no runs in the given week", () => {
    const { container } = render(
      <WeekRunningStats
        runningActivities={[activity("2025-05-19")]} // a different week
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("sums this week's own runs for the headline stats, ignoring runs from other weeks", () => {
    render(
      <WeekRunningStats
        runningActivities={[
          activity("2025-06-02", { distance_m: 5000, duration_s: 1800, moving_duration_s: 1800 }),
          activity("2025-06-04", { distance_m: 10000, duration_s: 3000, moving_duration_s: 3000 }),
          activity("2025-05-26", { distance_m: 20000 }), // prior week, must not be counted
        ]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    expect(screen.getByText("15.0")).toBeInTheDocument(); // total distance, km
  });

  it("excludes an implausibly slow 'run' (a real device/export mislabel, e.g. a hike) from this week's totals and the scatter backdrop", () => {
    const { container } = render(
      <WeekRunningStats
        runningActivities={[
          activity("2025-06-02", { id: "real-run", distance_m: 5000, duration_s: 1800, moving_duration_s: 1800 }), // 6:00/km
          activity("2025-06-03", {
            id: "mislabeled-hike",
            distance_m: 1770,
            duration_s: 1160,
            moving_duration_s: 1160,
          }), // ~10.9 min/km -- past the real gap, not a genuine run
        ]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    // Total distance should reflect only the real run (5.0km), not 6.77km including the hike.
    expect(screen.getByText("5.0")).toBeInTheDocument();
    expect(screen.queryByText("6.8")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(1);
  });

  it("sums this week's own runs' elevation gain, only when there's real gain to show", () => {
    const { rerender } = render(
      <WeekRunningStats
        runningActivities={[
          activity("2025-06-02", { elevation_gain_m: 120 }),
          activity("2025-06-04", { elevation_gain_m: 80 }),
          activity("2025-05-26", { elevation_gain_m: 500 }), // prior week, must not be counted
        ]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    expect(screen.getByText("Total elevation")).toBeInTheDocument();
    expect(screen.getByText("200")).toBeInTheDocument();

    rerender(
      <WeekRunningStats
        runningActivities={[activity("2025-06-02", { elevation_gain_m: null })]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    expect(screen.queryByText("Total elevation")).not.toBeInTheDocument();
  });

  it("shows the prior week's own running total as a caption on Total distance, not a +/- delta", () => {
    const { rerender } = render(
      <WeekRunningStats
        runningActivities={[activity("2025-06-02", { distance_m: 10000 })]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    // No runs at all in the prior week's date range -> a real zero, not a delta.
    expect(screen.getByText("0.0km previous week")).toBeInTheDocument();

    rerender(
      <WeekRunningStats
        runningActivities={[
          activity("2025-06-02", { id: "this-week", distance_m: 10000 }),
          activity("2025-05-28", { id: "prior-week", distance_m: 5300 }),
        ]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    expect(screen.getByText("5.3km previous week")).toBeInTheDocument();
    expect(screen.queryByText("vs last week")).not.toBeInTheDocument();
  });

  it("shows Total METs and a per-day breakdown only for runs with both calories and weight", () => {
    render(
      <WeekRunningStats
        runningActivities={[
          activity("2025-06-03", { calories: 400, weight_kg: 80 }), // Tuesday: 300 MET-min
          activity("2025-06-05", { calories: null }), // missing calories -- excluded
        ]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    expect(screen.getByText("Total METs")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText(/Tue 300 METs/)).toBeInTheDocument();
  });

  it("paints this week's runs last in the pace-vs-distance scatter, so they're never covered by faded background points", () => {
    // Real API ordering is newest-first, so this week's own run (the most recent one) would
    // naturally come *first* in the array -- SVG paints later elements on top of earlier ones,
    // so without re-sorting, this week's solid dot would render underneath the faded older ones
    // drawn after it. `runningActivities` here reproduces that real ordering deliberately.
    const { container } = render(
      <WeekRunningStats
        runningActivities={[
          activity("2025-06-04", { id: "this-week-run" }), // newest, this week
          activity("2025-05-20", { id: "older-run-1" }),
          activity("2025-05-10", { id: "older-run-2" }),
        ]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    const cells = [...container.querySelectorAll(".recharts-scatter-symbol path")];
    const opacities = cells.map((c) => c.getAttribute("fill-opacity"));
    // The last-painted (topmost) point must be this week's solid one, not a faded older run.
    expect(opacities.at(-1)).toBe("1");
    expect(opacities.slice(0, -1).every((o) => o === "0.35")).toBe(true);
  });

  it("shows the all-time-PR headline only when this week's own best matches an all-time record", () => {
    const thisWeekRun = activity("2025-06-02", {
      id: "pr-run",
      distance_m: 5000,
      duration_s: 1500,
      moving_duration_s: 1500,
    });
    const { rerender } = render(
      <WeekRunningStats
        runningActivities={[thisWeekRun]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    expect(screen.queryByText(/all-time PR/)).not.toBeInTheDocument();

    rerender(
      <WeekRunningStats
        runningActivities={[thisWeekRun]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
        allTimeRecords={[
          {
            label: "5 km",
            date: "2025-06-02", // same date as thisWeekRun's own best 5k -> a genuine all-time PR
            activityId: "thisweekrun",
            actualDistanceM: 5000,
            durationS: 1500,
            paceMinPerKm: 5,
            speedKmh: 12,
            eligibleCount: 1,
          },
        ]}
      />,
    );
    expect(screen.getByText(/1 all-time PR set this week: 5 km/)).toBeInTheDocument();
  });

  it("omits Total METs entirely when no run this week has both calories and weight", () => {
    render(
      <WeekRunningStats
        runningActivities={[activity("2025-06-02", { calories: null, weight_kg: null })]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekStart="2025-05-26"
        priorWeekEnd="2025-06-01"
      />,
    );
    expect(screen.queryByText("Total METs")).not.toBeInTheDocument();
  });
});
