import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "../api/types";
import { PaceTrendsChart } from "./PaceTrendsChart";

function activity(
  local_date: string,
  vdot: number,
  overrides: Partial<ActivitySummary> = {},
): ActivitySummary {
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
    calories: null,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    vdot,
    workout_name: null,
    primary_source: "test",
    stream_available: false,
    climb_route_count: null,
    climb_max_completed_grade: null,
    climb_time_s: null,
    ...overrides,
  };
}

// One run roughly every two months across five years -- enough points for a click-move-click
// selection over part of the duration chart's x-range to land a strictly smaller sub-range
// than the full history.
function fixtureActivities(): ActivitySummary[] {
  const activities: ActivitySummary[] = [];
  for (let year = 2020; year <= 2024; year++) {
    for (const month of [1, 3, 5, 7, 9, 11]) {
      const localDate = `${year}-${String(month).padStart(2, "0")}-15`;
      activities.push(activity(localDate, 40 + ((year - 2020) * 6 + month) * 0.1));
    }
  }
  return activities;
}

const raf = () =>
  act(async () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));

/** Recharts throttles its own mousemove-driven index tracking via requestAnimationFrame (see
 * PaceTrendsChart.tsx's own `toDataIndex` comment for the sibling activeIndex-type gotcha this
 * uncovered) -- a click's own index resolution depends on that same tracked state having
 * already settled, so every point in a click-move-click sequence needs a real mousemove (with a
 * settled frame after it) immediately before the click that's meant to land there. */
async function clickAt(element: Element, clientX: number, clientY: number): Promise<void> {
  fireEvent.mouseEnter(element, { clientX, clientY });
  fireEvent.mouseMove(element, { clientX, clientY });
  await raf();
  await raf();
  fireEvent.click(element, { clientX, clientY });
}

describe("PaceTrendsChart", () => {
  it("renders nothing when there are no VDOT-eligible runs", () => {
    const { container } = render(<PaceTrendsChart activities={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("starts showing the full history, un-zoomed", () => {
    const activities = fixtureActivities();
    render(<PaceTrendsChart activities={activities} />);
    expect(screen.getByText("2020-01-15")).toBeInTheDocument();
    expect(screen.getByText("2024-11-15")).toBeInTheDocument();
    expect(screen.queryByText("Reset to full history")).not.toBeInTheDocument();
  });

  it("translates the VDOT/week trend into an estimated 5K finish-time change", () => {
    // fixtureActivities()' vdot values rise monotonically over time, so the trend is
    // improving -- the caption should read "Cut ... from your estimated 5K finish time", not
    // "Added ... to".
    const activities = fixtureActivities();
    render(<PaceTrendsChart activities={activities} />);
    const caption = document.querySelector(".pace-trends__selection-bar span")!.textContent;
    expect(caption).toContain("VDOT/week");
    expect(caption).toMatch(/Cut \d+:\d{2} from your estimated 5K finish time/);
  });

  it("click, move, click on the duration chart zooms the trend chart to that period", async () => {
    const activities = fixtureActivities();
    const { container } = render(<PaceTrendsChart activities={activities} />);

    // The duration BarChart is the second Recharts root in the DOM (the VDOT ComposedChart is
    // the first) -- its wrapper div is what carries the onClick/onMouseMove props this
    // click-move-click selection is built on.
    const wrappers = container.querySelectorAll(".recharts-wrapper");
    expect(wrappers).toHaveLength(2);
    const durationWrapper = wrappers[1]!;

    await clickAt(durationWrapper, 200, 90); // first click: start the period
    await clickAt(durationWrapper, 600, 90); // second click: finish it

    expect(screen.getByText("Reset to full history")).toBeInTheDocument();
    // The visible range must be a strict subset of the full 2020-01-15..2024-11-15 history --
    // both boundary dates narrowing is the strongest signal the selection actually committed a
    // partial range rather than snapping back to (or never leaving) the full one.
    expect(screen.queryByText("2020-01-15")).not.toBeInTheDocument();
    expect(screen.queryByText("2024-11-15")).not.toBeInTheDocument();
  });

  it("clicking the same point twice cancels rather than selecting a zero-width range", async () => {
    const activities = fixtureActivities();
    render(<PaceTrendsChart activities={activities} />);
    const durationWrapper = document.querySelectorAll(".recharts-wrapper")[1]!;

    await clickAt(durationWrapper, 400, 90);
    await clickAt(durationWrapper, 400, 90);

    expect(screen.queryByText("Reset to full history")).not.toBeInTheDocument();
    expect(screen.getByText("2020-01-15")).toBeInTheDocument();
    expect(screen.getByText("2024-11-15")).toBeInTheDocument();
  });

  it("a third click after a completed selection starts a brand new one", async () => {
    const activities = fixtureActivities();
    render(<PaceTrendsChart activities={activities} />);
    const durationWrapper = document.querySelectorAll(".recharts-wrapper")[1]!;

    await clickAt(durationWrapper, 200, 90);
    await clickAt(durationWrapper, 600, 90);
    expect(screen.getByText("Reset to full history")).toBeInTheDocument();
    const firstSelectionCaption = document.querySelector(".pace-trends__selection-bar span")!
      .textContent;

    await clickAt(durationWrapper, 300, 90);
    await clickAt(durationWrapper, 500, 90);

    expect(screen.getByText("Reset to full history")).toBeInTheDocument();
    const secondSelectionCaption = document.querySelector(".pace-trends__selection-bar span")!
      .textContent;
    expect(secondSelectionCaption).not.toBe(firstSelectionCaption);
  });

  it("Reset to full history clears the selection", async () => {
    const activities = fixtureActivities();
    render(<PaceTrendsChart activities={activities} />);
    const durationWrapper = document.querySelectorAll(".recharts-wrapper")[1]!;

    await clickAt(durationWrapper, 200, 90);
    await clickAt(durationWrapper, 600, 90);

    const resetButton = screen.getByText("Reset to full history");
    fireEvent.click(resetButton);

    expect(screen.getByText("2020-01-15")).toBeInTheDocument();
    expect(screen.getByText("2024-11-15")).toBeInTheDocument();
    expect(screen.queryByText("Reset to full history")).not.toBeInTheDocument();
  });
});
