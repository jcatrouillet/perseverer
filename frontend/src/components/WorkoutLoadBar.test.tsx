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
  estimated_distance_m: 3000,
  estimated_load: 42,
  segments: [
    { duration_s: 600, zone: 1 },
    { duration_s: 300, zone: 5 },
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

  it("renders a neutral segment for a zone-less step (e.g. rest)", () => {
    const { container } = render(
      <WorkoutLoadBar
        workout={{ ...BASE, segments: [{ duration_s: 60, zone: null }, { duration_s: 60, zone: 3 }] }}
      />,
    );
    const segments = container.querySelectorAll(".workout-load-bar__segment");
    expect(segments).toHaveLength(2);
  });
});
