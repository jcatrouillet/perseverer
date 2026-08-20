import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "../api/types";
import { computeTrainingBands, TrainingBandsChart } from "./TrainingBandsChart";

function activity(overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    id: "a1",
    start_time_utc: "2026-01-15T12:00:00Z",
    utc_offset_s: 0,
    local_date: "2026-01-15",
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
    ...overrides,
  };
}

describe("computeTrainingBands", () => {
  it("buckets a run by its own whole-activity average pace", () => {
    // 5000m in 1500s = 5:00/km exactly.
    const rows = computeTrainingBands([activity({ id: "r1", distance_m: 5000, duration_s: 1500, moving_duration_s: 1500 })]);
    const hit = rows.find((r) => r.seconds > 0);
    expect(hit?.label).toBe("5:00–5:30");
    expect(hit?.pct).toBe(100);
  });

  it("routes an implausibly slow run into the Walk band", () => {
    // 5000m in 3000s = 10:00/km -- well past the 8:30/km plausible-run cutoff.
    const rows = computeTrainingBands([activity({ id: "r1", distance_m: 5000, duration_s: 3000, moving_duration_s: 3000 })]);
    const walk = rows.find((r) => r.label === "Walk");
    expect(walk?.seconds).toBe(3000);
    expect(walk?.pct).toBe(100);
  });

  it("splits time proportionally across two differently-paced runs", () => {
    const fast = activity({ id: "r1", distance_m: 5000, duration_s: 1500, moving_duration_s: 1500 }); // 5:00/km, 1500s
    const slow = activity({ id: "r2", distance_m: 5000, duration_s: 1500 * 3, moving_duration_s: 1500 * 3 }); // 15:00/km -> Walk, 4500s
    const rows = computeTrainingBands([fast, slow]);
    const fastRow = rows.find((r) => r.label === "5:00–5:30")!;
    const walkRow = rows.find((r) => r.label === "Walk")!;
    expect(fastRow.seconds).toBe(1500);
    expect(walkRow.seconds).toBe(4500);
    expect(Math.round(fastRow.pct)).toBe(25);
    expect(Math.round(walkRow.pct)).toBe(75);
  });

  it("skips activities with no usable duration or distance", () => {
    const rows = computeTrainingBands([activity({ distance_m: null }), activity({ duration_s: null, moving_duration_s: null })]);
    expect(rows.every((r) => r.seconds === 0)).toBe(true);
  });
});

describe("TrainingBandsChart", () => {
  it("renders nothing when there is no bucketable data", () => {
    const { container } = render(<TrainingBandsChart activities={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the chart title once there is real data", () => {
    render(<TrainingBandsChart activities={[activity()]} />);
    expect(screen.getByText("Training bands")).toBeInTheDocument();
  });

  it("defaults to Percentage and switches to Duration on click", () => {
    render(<TrainingBandsChart activities={[activity()]} />);
    const percentageButton = screen.getByText("Percentage");
    const durationButton = screen.getByText("Duration");
    expect(percentageButton.className).toContain("is-active");
    expect(durationButton.className).not.toContain("is-active");

    fireEvent.click(durationButton);
    expect(durationButton.className).toContain("is-active");
    expect(percentageButton.className).not.toContain("is-active");
  });
});
