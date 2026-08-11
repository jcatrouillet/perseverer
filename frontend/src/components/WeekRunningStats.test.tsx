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
        priorWeekDistanceM={null}
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
        priorWeekDistanceM={null}
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
        priorWeekDistanceM={null}
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
        priorWeekDistanceM={null}
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
        priorWeekDistanceM={null}
      />,
    );
    expect(screen.queryByText("Total elevation")).not.toBeInTheDocument();
  });

  it("shows the vs-last-week delta with a sign, only when a prior week total is available", () => {
    const { rerender } = render(
      <WeekRunningStats
        runningActivities={[activity("2025-06-02", { distance_m: 10000 })]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekDistanceM={null}
      />,
    );
    expect(screen.queryByText("vs last week")).not.toBeInTheDocument();

    rerender(
      <WeekRunningStats
        runningActivities={[activity("2025-06-02", { distance_m: 10000 })]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekDistanceM={5300}
      />,
    );
    expect(screen.getByText("vs last week")).toBeInTheDocument();
    expect(screen.getByText("+4.7")).toBeInTheDocument();
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
        priorWeekDistanceM={null}
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
        priorWeekDistanceM={null}
      />,
    );
    const cells = [...container.querySelectorAll(".recharts-scatter-symbol path")];
    const opacities = cells.map((c) => c.getAttribute("fill-opacity"));
    // The last-painted (topmost) point must be this week's solid one, not a faded older run.
    expect(opacities.at(-1)).toBe("1");
    expect(opacities.slice(0, -1).every((o) => o === "0.35")).toBe(true);
  });

  it("omits Total METs entirely when no run this week has both calories and weight", () => {
    render(
      <WeekRunningStats
        runningActivities={[activity("2025-06-02", { calories: null, weight_kg: null })]}
        rangeStart="2025-05-01"
        rangeEnd="2025-06-08"
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
        priorWeekDistanceM={null}
      />,
    );
    expect(screen.queryByText("Total METs")).not.toBeInTheDocument();
  });
});
