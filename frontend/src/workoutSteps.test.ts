import { describe, expect, it } from "vitest";

import type { ActivityWorkoutStepOut, LapOut, PlannedWorkoutStepOut } from "./api/types";
import {
  alignLapsToWorkoutSteps,
  estimatedStepDistanceM,
  expandWorkoutSteps,
  formatStepDistanceKm,
  formatStepDurationLabel,
  groupWorkoutStepsForDisplay,
  plannedExerciseLabel,
  targetPaceRangeLabel,
} from "./workoutSteps";

function plannedStep(overrides: Partial<PlannedWorkoutStepOut>): PlannedWorkoutStepOut {
  return {
    step_index: 0,
    duration_type: null,
    duration_time_s: null,
    duration_distance_m: null,
    target_type: null,
    target_low: null,
    target_high: null,
    target_hr_zone: null,
    cadence_low: null,
    cadence_high: null,
    intensity: null,
    repeat_from_step: null,
    repeat_count: null,
    duration_reps: null,
    exercise_category: null,
    exercise_name: null,
    weight_kg: null,
    comment: null,
    ...overrides,
  };
}

// Direct transcription of a real structured-workout FIT file (a warmup, a 5x-repeated [1km
// interval, 75s recovery] block, and a cooldown, each with a target pace range) -- see
// fit/parser.py::_parse_workout's own docstring for the confirmation.
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

const WARMUP = step({
  step_index: 0,
  duration_type: "time",
  duration_time_s: 900,
  target_type: "speed",
  target_low_mps: 2.439,
  target_high_mps: 2.597,
  intensity: "warmup",
});
const INTERVAL = step({
  step_index: 1,
  duration_type: "distance",
  duration_distance_m: 1000,
  target_type: "speed",
  target_low_mps: 3.226,
  target_high_mps: 3.333,
  intensity: "active",
});
const RECOVERY = step({
  step_index: 2,
  duration_type: "time",
  duration_time_s: 75,
  target_type: "speed",
  target_low_mps: 2.381,
  target_high_mps: 2.564,
  intensity: "active",
});
const REPEAT = step({
  step_index: 3,
  duration_type: "repeat_until_steps_cmplt",
  repeat_from_step: 1,
  repeat_count: 5,
});
const COOLDOWN = step({
  step_index: 4,
  duration_type: "time",
  duration_time_s: 600,
  target_type: "speed",
  target_low_mps: 2.439,
  target_high_mps: 2.597,
  intensity: "cooldown",
});

const WORKOUT_STEPS = [WARMUP, INTERVAL, RECOVERY, REPEAT, COOLDOWN];

describe("expandWorkoutSteps", () => {
  it("replaces the repeat block with repeat_count copies of its children, in order", () => {
    const expanded = expandWorkoutSteps(WORKOUT_STEPS);
    expect(expanded.map((s) => s.step_index)).toEqual([0, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 4]);
    // 12 expanded steps -- matches the real activity's 13 recorded laps minus the device's own
    // trailing auto-generated "stop" lap.
    expect(expanded).toHaveLength(12);
  });

  it("passes a workout with no repeat block through unchanged", () => {
    const expanded = expandWorkoutSteps([WARMUP, COOLDOWN]);
    expect(expanded).toEqual([WARMUP, COOLDOWN]);
  });

  it("handles an empty step list", () => {
    expect(expandWorkoutSteps([])).toEqual([]);
  });
});

describe("groupWorkoutStepsForDisplay", () => {
  it("groups the repeated interval/recovery pair under a single 5x header", () => {
    const groups = groupWorkoutStepsForDisplay(WORKOUT_STEPS);
    expect(groups.map((g) => g.label)).toEqual(["Warmup", "5x", "Cooldown"]);
    expect(groups[1]!.repeatCount).toBe(5);
    expect(groups[1]!.steps.map((s) => s.step_index)).toEqual([1, 2]);
  });

  it("capitalizes an unrecognized intensity rather than leaving a group unlabeled", () => {
    const groups = groupWorkoutStepsForDisplay([step({ step_index: 0, intensity: "rest" })]);
    expect(groups[0]!.label).toBe("Rest");
  });

  it("falls back to a generic label when intensity is null", () => {
    const groups = groupWorkoutStepsForDisplay([step({ step_index: 0, intensity: null })]);
    expect(groups[0]!.label).toBe("Step");
  });
});

describe("targetPaceRangeLabel", () => {
  it("formats a speed-target step's range as faster-slower pace, matching real Garmin numbers", () => {
    // 2.439-2.597 m/s -> 6:50-6:25 (high speed = fast/low pace shown first).
    expect(targetPaceRangeLabel(WARMUP, "running")).toBe("6:25-6:50");
    expect(targetPaceRangeLabel(INTERVAL, "running")).toBe("5:00-5:10");
    expect(targetPaceRangeLabel(RECOVERY, "running")).toBe("6:30-7:00");
  });

  it("returns null for a step with no speed target (e.g. the repeat marker itself)", () => {
    expect(targetPaceRangeLabel(REPEAT, "running")).toBeNull();
  });

  it("returns null for a non-pace sport", () => {
    expect(targetPaceRangeLabel(WARMUP, "cycling")).toBeNull();
  });
});

describe("estimatedStepDistanceM", () => {
  it("estimates a time-based step's distance from its target range's low speed bound", () => {
    // 900s * 2.439 m/s = 2195.1m -> the real "2.2km" Garmin shows for this exact step.
    expect(estimatedStepDistanceM(WARMUP)).toBeCloseTo(2195.1, 1);
    // 75s * 2.381 m/s = 178.575m -> the real "0.18km" Garmin shows for this exact step.
    expect(estimatedStepDistanceM(RECOVERY)).toBeCloseTo(178.575, 1);
  });

  it("returns null for a distance-based step (already has a real target distance)", () => {
    expect(estimatedStepDistanceM(INTERVAL)).toBeNull();
  });

  it("returns null for a step with no time duration or no target range", () => {
    expect(estimatedStepDistanceM(REPEAT)).toBeNull();
  });
});

describe("formatStepDistanceKm", () => {
  it("uses 2 decimals under 1km and 1 decimal at or above, matching real Garmin figures", () => {
    expect(formatStepDistanceKm(2195.1)).toBe("2.2km");
    expect(formatStepDistanceKm(178.575)).toBe("0.18km");
    expect(formatStepDistanceKm(1463.4)).toBe("1.5km");
  });
});

describe("formatStepDurationLabel", () => {
  it("shows an exact multiple of 60s in minutes", () => {
    expect(formatStepDurationLabel(WARMUP)).toBe("15m");
    expect(formatStepDurationLabel(COOLDOWN)).toBe("10m");
  });

  it("shows a non-round duration in raw seconds, not minutes", () => {
    expect(formatStepDurationLabel(RECOVERY)).toBe("75s");
  });

  it("shows a distance-based step's target distance in km", () => {
    expect(formatStepDurationLabel(INTERVAL)).toBe("1km");
  });

  it("labels a bare lap-button step by the button, not a duration", () => {
    expect(formatStepDurationLabel(plannedStep({ duration_type: "lap_button" }))).toBe(
      "Lap button",
    );
  });

  it("marks a lap-button step's distance/time as an estimate, since the watch never advances on it", () => {
    expect(
      formatStepDurationLabel(
        plannedStep({ duration_type: "lap_button", duration_distance_m: 5000 }),
      ),
    ).toBe("Lap button (~5km)");
    expect(
      formatStepDurationLabel(plannedStep({ duration_type: "lap_button", duration_time_s: 2400 })),
    ).toBe("Lap button (~40m)");
  });

  it("returns null for a step with neither a time nor a distance duration", () => {
    expect(formatStepDurationLabel(REPEAT)).toBeNull();
  });

  it("shows a hiit/strength_training reps-based step's own rep count", () => {
    expect(formatStepDurationLabel(plannedStep({ duration_type: "reps", duration_reps: 10 }))).toBe(
      "10 reps",
    );
  });

  it("returns null for a reps-type step missing its own duration_reps", () => {
    expect(formatStepDurationLabel(plannedStep({ duration_type: "reps" }))).toBeNull();
  });
});

describe("plannedExerciseLabel", () => {
  it("prefers the specific exercise_name over the bare category", () => {
    expect(
      plannedExerciseLabel(
        plannedStep({ exercise_category: "CURL", exercise_name: "HAMMER_CURL" }),
      ),
    ).toBe("Hammer Curl");
  });

  it("falls back to the category when exercise_name is the empty string", () => {
    expect(
      plannedExerciseLabel(plannedStep({ exercise_category: "BENCH_PRESS", exercise_name: "" })),
    ).toBe("Bench Press");
  });

  it("labels a rest step as Rest even with no exercise fields at all", () => {
    expect(plannedExerciseLabel(plannedStep({ intensity: "rest" }))).toBe("Rest");
  });

  it("falls back to a generic 'Exercise' label when nothing at all is set", () => {
    expect(plannedExerciseLabel(plannedStep({}))).toBe("Exercise");
  });
});

function lap(overrides: Partial<LapOut>): LapOut {
  return {
    lap_index: 0,
    start_time_utc: "2026-01-01T00:00:00Z",
    duration_s: null,
    moving_duration_s: null,
    distance_m: null,
    avg_hr: null,
    max_hr: null,
    avg_speed_mps: null,
    avg_gap_speed_mps: null,
    ...overrides,
  };
}

describe("alignLapsToWorkoutSteps", () => {
  it("maps one lap per step in the ordinary one-lap-per-step case", () => {
    const steps = [
      step({ step_index: 0, duration_type: "time", duration_time_s: 900 }),
      step({ step_index: 1, duration_type: "distance", duration_distance_m: 1000 }),
    ];
    const laps = [lap({ moving_duration_s: 898 }), lap({ distance_m: 1002 })];
    expect(alignLapsToWorkoutSteps(laps, steps)).toEqual([steps[0], steps[1]]);
  });

  it("merges two laps split by an autolap mid-step back onto the same step", () => {
    // The reported bug: a 10-minute step, but the watch's own 1km autolap fires mid-step,
    // splitting the recorded laps into a 6:10 lap and a 3:50 lap (totaling the planned 10:00)
    // before the real next step's own lap.
    const tenMinuteStep = step({ step_index: 0, duration_type: "time", duration_time_s: 600 });
    const nextStep = step({ step_index: 1, duration_type: "time", duration_time_s: 300 });
    const laps = [
      lap({ moving_duration_s: 6 * 60 + 10 }),
      lap({ moving_duration_s: 3 * 60 + 50 }),
      lap({ moving_duration_s: 300 }),
    ];
    expect(alignLapsToWorkoutSteps(laps, [tenMinuteStep, nextStep])).toEqual([
      tenMinuteStep,
      tenMinuteStep,
      nextStep,
    ]);
  });

  it("merges an autolap split on a distance-based step the same way", () => {
    const oneKmStep = step({ step_index: 0, duration_type: "distance", duration_distance_m: 2000 });
    const laps = [lap({ distance_m: 1000 }), lap({ distance_m: 1005 })];
    expect(alignLapsToWorkoutSteps(laps, [oneKmStep])).toEqual([oneKmStep, oneKmStep]);
  });

  it("maps every lap past the last step to null, e.g. a device's own trailing stop lap", () => {
    const onlyStep = step({ step_index: 0, duration_type: "time", duration_time_s: 600 });
    const laps = [lap({ moving_duration_s: 600 }), lap({ moving_duration_s: 5 })];
    expect(alignLapsToWorkoutSteps(laps, [onlyStep])).toEqual([onlyStep, null]);
  });

  it("advances after exactly one lap for a step with no time/distance target", () => {
    const openStep = step({ step_index: 0, duration_type: null });
    const nextStep = step({ step_index: 1, duration_type: null });
    const laps = [lap({ moving_duration_s: 60 }), lap({ moving_duration_s: 60 })];
    expect(alignLapsToWorkoutSteps(laps, [openStep, nextStep])).toEqual([openStep, nextStep]);
  });

  it("returns an empty array for laps when there are no expected steps at all", () => {
    expect(alignLapsToWorkoutSteps([lap({})], [])).toEqual([null]);
  });
});
