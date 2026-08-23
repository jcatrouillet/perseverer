import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "../api/types";
import { PeriodStatsCard } from "./PeriodStatsCard";

function activity(overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    id: "act1",
    start_time_utc: "2025-06-01T13:00:00Z",
    utc_offset_s: 0,
    local_date: "2025-06-01",
    sport: "running",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m: 5000,
    elevation_gain_m: null,
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

describe("PeriodStatsCard activities-by-type breakdown", () => {
  it("shows a pie chart with no legend, defaulting to # activities", () => {
    render(
      <PeriodStatsCard
        title="2026"
        activities={[
          activity({ id: "r1", sport: "running", duration_s: 1800, moving_duration_s: 1800 }),
          activity({ id: "r2", sport: "running", duration_s: 1800, moving_duration_s: 1800 }),
          activity({ id: "c1", sport: "cycling", duration_s: 3600, moving_duration_s: 3600 }),
        ]}
        busiestLabel="Busiest month"
        busiestValue={null}
      />,
    );

    // getByRole, not getByText: ChartFullscreen's mobile-only "tap to expand" affordance
    // (PeriodStatsCard.tsx) renders the title twice -- a real button (mobile) and an
    // aria-hidden static span (desktop), toggled by CSS media query rather than JS, so both
    // exist in the DOM regardless of test viewport. getByRole's accessible-name computation
    // correctly excludes the aria-hidden copy; getByText would match both and throw.
    expect(screen.getByRole("heading", { name: "Activities by type" })).toBeInTheDocument();
    expect(document.querySelector(".recharts-pie")).not.toBeNull();
    // No legend/list beside the pie -- reading values is hover-only via the Tooltip.
    expect(document.querySelector(".type-list")).toBeNull();
    expect(screen.queryByText("running")).toBeNull();
    expect(screen.queryByText("cycling")).toBeNull();
  });

  it("switches the pie's dataKey to durationS when the Time toggle is clicked", () => {
    render(
      <PeriodStatsCard
        title="2026"
        activities={[
          activity({ id: "r1", sport: "running", duration_s: 1800, moving_duration_s: 1800 }),
          activity({ id: "r2", sport: "running", duration_s: 1800, moving_duration_s: 1800 }),
        ]}
        busiestLabel="Busiest month"
        busiestValue={null}
      />,
    );

    const timeButton = screen.getByText("Time");
    expect(timeButton.className).not.toContain("is-active");
    fireEvent.click(timeButton);
    expect(timeButton.className).toContain("is-active");
  });

  it("shows the sport name and value in the tooltip when hovering a slice", () => {
    // Regression test: Pie tooltips have no natural axis label, so `labelFormatter` never
    // fires for them -- the sport name has to come through `formatter`'s own second argument
    // instead. A prior version relied on `labelFormatter` and silently rendered an empty label
    // (only the bare value), confirmed by hovering a real slice in the browser.
    const { container } = render(
      <PeriodStatsCard
        title="2026"
        activities={[
          activity({ id: "r1", sport: "cycling", duration_s: 1800, moving_duration_s: 1800 }),
          activity({ id: "r2", sport: "cycling", duration_s: 1800, moving_duration_s: 1800 }),
        ]}
        busiestLabel="Busiest month"
        busiestValue={null}
      />,
    );

    const sector = container.querySelector(".recharts-pie-sector path");
    expect(sector).not.toBeNull();
    fireEvent.mouseOver(sector!);
    fireEvent.mouseMove(sector!);

    expect(document.querySelector(".recharts-tooltip-wrapper")?.textContent).toContain("cycling");
    expect(document.querySelector(".recharts-tooltip-wrapper")?.textContent).toContain("2");
  });
});
