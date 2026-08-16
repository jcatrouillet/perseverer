import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityWorkoutOut, ActivityWorkoutStepOut } from "../api/types";
import { WorkoutPlan } from "./WorkoutPlan";

function step(overrides: Partial<ActivityWorkoutStepOut>): ActivityWorkoutStepOut {
  return {
    step_index: 0,
    duration_type: null,
    duration_time_s: null,
    duration_distance_m: null,
    target_type: null,
    target_low_mps: null,
    target_high_mps: null,
    intensity: null,
    repeat_from_step: null,
    repeat_count: null,
    ...overrides,
  };
}

// Direct transcription of a real structured-workout FIT file -- see workoutSteps.test.ts.
const REAL_WORKOUT: ActivityWorkoutOut = {
  name: "W9 Tue · 5x1km Threshold",
  description: "Focus: Lactate threshold.",
  steps: [
    step({
      step_index: 0,
      duration_type: "time",
      duration_time_s: 900,
      target_type: "speed",
      target_low_mps: 2.439,
      target_high_mps: 2.597,
      intensity: "warmup",
    }),
    step({
      step_index: 1,
      duration_type: "distance",
      duration_distance_m: 1000,
      target_type: "speed",
      target_low_mps: 3.226,
      target_high_mps: 3.333,
      intensity: "active",
    }),
    step({
      step_index: 2,
      duration_type: "time",
      duration_time_s: 75,
      target_type: "speed",
      target_low_mps: 2.381,
      target_high_mps: 2.564,
      intensity: "active",
    }),
    step({ step_index: 3, duration_type: "repeat_until_steps_cmplt", repeat_from_step: 1, repeat_count: 5 }),
    step({
      step_index: 4,
      duration_type: "time",
      duration_time_s: 600,
      target_type: "speed",
      target_low_mps: 2.439,
      target_high_mps: 2.597,
      intensity: "cooldown",
    }),
  ],
};

describe("WorkoutPlan", () => {
  it("renders the workout name and the Warmup/5x/Cooldown group structure", () => {
    render(<WorkoutPlan workout={REAL_WORKOUT} sport="running" />);
    expect(screen.getByText("W9 Tue · 5x1km Threshold")).toBeInTheDocument();
    expect(screen.getByText("Warmup")).toBeInTheDocument();
    expect(screen.getByText("5x")).toBeInTheDocument();
    expect(screen.getByText("Cooldown")).toBeInTheDocument();
  });

  it("shows each step's duration, target pace range, and estimated distance", () => {
    render(<WorkoutPlan workout={REAL_WORKOUT} sport="running" />);
    expect(screen.getByText(/15m 6:25-6:50 \(2.2km\) Pace/)).toBeInTheDocument();
    expect(screen.getByText(/1km 5:00-5:10 Pace/)).toBeInTheDocument();
    expect(screen.getByText(/75s 6:30-7:00 \(0.18km\) Pace/)).toBeInTheDocument();
  });

  it("renders nothing for a non-pace sport", () => {
    const { container } = render(<WorkoutPlan workout={REAL_WORKOUT} sport="cycling" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the workout has no steps", () => {
    const { container } = render(
      <WorkoutPlan workout={{ name: null, description: null, steps: [] }} sport="running" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("falls back to a generic heading when the workout has no name", () => {
    render(<WorkoutPlan workout={{ ...REAL_WORKOUT, name: null }} sport="running" />);
    expect(screen.getByText("Workout plan")).toBeInTheDocument();
  });
});
