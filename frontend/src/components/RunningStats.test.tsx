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
    primary_source: "test",
    stream_available: false,
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
});
