import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PlannedWorkoutOut } from "../api/types";
import { WorkoutLoadBar } from "./WorkoutLoadBar";

const BASE: PlannedWorkoutOut = {
  available: true,
  id: 1,
  local_date: "2026-09-01",
  sport: "running",
  name: "Tempo run",
  source_text: "Warmup 10m",
  scheduled_time: null,
  estimated_duration_s: 900,
  steps: [],
  parse_errors: [],
  push_status: "draft",
  push_error: null,
  garmin_workout_id: null,
  garmin_scheduled_at: null,
  completed_at: null,
  estimated_distance_m: 3000,
  estimated_load: 42,
  segments: [
    { duration_s: 600, zone: 1, intensity_factor: 0.75 },
    { duration_s: 300, zone: 5, intensity_factor: 1.2 },
  ],
};

describe("WorkoutLoadBar", () => {
  it("renders nothing for a non-running sport", () => {
    const { container } = render(<WorkoutLoadBar workout={{ ...BASE, sport: "yoga" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there are no segments", () => {
    const { container } = render(<WorkoutLoadBar workout={{ ...BASE, segments: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the distance/duration line in the athlete's own requested format", () => {
    render(<WorkoutLoadBar workout={BASE} />);
    expect(screen.getByText("Distance: 3.0km - Duration: 15 minutes")).toBeInTheDocument();
  });

  it("shows the load number when estimated_load is available", () => {
    render(<WorkoutLoadBar workout={BASE} />);
    expect(screen.getByText("Load 42")).toBeInTheDocument();
  });

  it("segment widths sum to 100% of the total duration", () => {
    const { container } = render(<WorkoutLoadBar workout={BASE} />);
    const segments = container.querySelectorAll(".workout-load-bar__segment");
    expect(segments).toHaveLength(2);
    const totalPct = [...segments].reduce((sum, el) => {
      const width = (el as HTMLElement).style.width;
      return sum + parseFloat(width);
    }, 0);
    expect(totalPct).toBeCloseTo(100, 5);
    expect((segments[0] as HTMLElement).style.width).toBe("66.66666666666666%");
  });

  it("shows a configure-threshold note instead of a load number when estimated_load is null", () => {
    render(<WorkoutLoadBar workout={{ ...BASE, estimated_load: null }} />);
    expect(screen.queryByText(/^Load \d/)).not.toBeInTheDocument();
    expect(screen.getByText(/Configure a threshold pace/)).toBeInTheDocument();
  });

  it("draws taller columns for a higher intensity_factor even within the same zone", () => {
    // The reported bug: 5:10-5:30/km and 4:50-5:15/km both land in zone 2 against a fast
    // threshold, so color alone can't distinguish them -- height must still differ.
    const { container } = render(
      <WorkoutLoadBar
        workout={{
          ...BASE,
          segments: [
            { duration_s: 180, zone: 2, intensity_factor: 0.878 },
            { duration_s: 20, zone: 2, intensity_factor: 0.929 },
          ],
        }}
      />,
    );
    const segments = container.querySelectorAll(".workout-load-bar__segment");
    const heights = [...segments].map((el) => parseFloat((el as HTMLElement).style.height));
    expect(heights[1]).toBeGreaterThan(heights[0]!);
  });

  it("renders a neutral segment for a zone-less step (e.g. rest)", () => {
    const { container } = render(
      <WorkoutLoadBar
        workout={{
          ...BASE,
          segments: [
            { duration_s: 60, zone: null, intensity_factor: null },
            { duration_s: 60, zone: 3, intensity_factor: 0.97 },
          ],
        }}
      />,
    );
    const segments = container.querySelectorAll(".workout-load-bar__segment");
    expect(segments).toHaveLength(2);
  });
});
