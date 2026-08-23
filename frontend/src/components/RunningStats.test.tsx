// Click-through behavior added in Milestone D: "when clicking on a table or graph should go to
// the corresponding activity" -- PR table rows link to /activities/:id (a PR names one exact
// activity), heatmap cells link to /day/:date (a cell represents a whole day, not one
// activity). The rest of RunningStats' large surface (charts, tiles) is already covered
// indirectly via other pages' own tests; this file is scoped to the new navigation behavior.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "../api/types";
import { RunningStats } from "./RunningStats";

function activity(id: string, local_date: string, overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    id,
    start_time_utc: `${local_date}T12:00:00Z`,
    utc_offset_s: 0,
    local_date,
    sport: "running",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 1500,
    moving_duration_s: 1500,
    distance_m: 5000,
    elevation_gain_m: null,
    calories: 350,
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

describe("RunningStats click-through", () => {
  it("links each personal-record row to that record's own activity", () => {
    const a = activity("act-5k", "2025-06-15");
    render(
      <RunningStats
        activities={[a]}
        startDate="2025-06-01"
        endDate="2025-06-30"
        periodLabel="June 2025"
      />,
    );
    const dateCell = screen.getByText("2025-06-15");
    const row = dateCell.closest("tr")!;
    expect(row.className).toContain("running-records__row");
  });

  it("links each daily heatmap cell to that day's /day/:date route", () => {
    const a = activity("act-5k", "2025-06-15");
    const { container } = render(
      <RunningStats
        activities={[a]}
        startDate="2025-06-01"
        endDate="2025-06-30"
        periodLabel="June 2025"
      />,
    );
    const cells = container.querySelectorAll(".running-heatmap__strip .running-heatmap__cell");
    const hrefs = Array.from(cells).map((c) => c.getAttribute("href"));
    expect(hrefs).toContain("/day/2025-06-15");
  });

  it("links Longest run, Best VDOT, and Max heart rate to the activity that set each", () => {
    // Three different activities, each the sole record-setter for one tile, so each link is
    // unambiguous.
    const activities = [
      activity("longest", "2025-06-10", { distance_m: 20000 }),
      activity("best-vdot", "2025-06-15", { distance_m: 5000, vdot: 45.0 }),
      activity("max-hr", "2025-06-20", { distance_m: 5000, max_hr_bpm: 188 }),
    ];
    render(
      <RunningStats
        activities={activities}
        startDate="2025-06-01"
        endDate="2025-06-30"
        periodLabel="June 2025"
      />,
    );
    expect(screen.getByText("Longest run").closest("a")).toHaveAttribute(
      "href",
      "/activities/longest",
    );
    expect(screen.getByText("Best VDOT").closest("a")).toHaveAttribute(
      "href",
      "/activities/best-vdot",
    );
    expect(screen.getByText("Max heart rate").closest("a")).toHaveAttribute(
      "href",
      "/activities/max-hr",
    );
    expect(screen.getByText("188")).toBeInTheDocument();
  });

  it("omits the Max heart rate tile when no activity in the period recorded one", () => {
    render(
      <RunningStats
        activities={[activity("a1", "2025-06-15")]}
        startDate="2025-06-01"
        endDate="2025-06-30"
        periodLabel="June 2025"
      />,
    );
    expect(screen.queryByText("Max heart rate")).not.toBeInTheDocument();
  });
});

describe("RunningStats best VDOT tile", () => {
  it("shows the period's best VDOT when at least one activity has one", () => {
    render(
      <RunningStats
        activities={[activity("a1", "2025-06-15", { vdot: 41.2 })]}
        startDate="2025-06-01"
        endDate="2025-06-30"
        periodLabel="June 2025"
      />,
    );
    expect(screen.getByText("Best VDOT")).toBeInTheDocument();
    expect(screen.getByText("41.2")).toBeInTheDocument();
  });

  it("omits the tile when no activity in the period has a VDOT", () => {
    render(
      <RunningStats
        activities={[activity("a1", "2025-06-15")]}
        startDate="2025-06-01"
        endDate="2025-06-30"
        periodLabel="June 2025"
      />,
    );
    expect(screen.queryByText("Best VDOT")).not.toBeInTheDocument();
  });
});
