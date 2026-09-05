import { beforeAll, describe, expect, it } from "vitest";

import type { PlannedWorkoutStepOut } from "../api/types";
import {
  apiStepsToEntries,
  emptyExerciseEntry,
  emptyRestEntry,
  entriesToApiSteps,
  estimateExerciseDurationS,
  preloadExerciseCatalog,
  searchExerciseCatalog,
} from "./ExerciseStepEditor";

beforeAll(async () => {
  await preloadExerciseCatalog();
});

function step(overrides: Partial<PlannedWorkoutStepOut>): PlannedWorkoutStepOut {
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
    ...overrides,
  };
}

describe("searchExerciseCatalog", () => {
  it("returns nothing for a too-short query", () => {
    expect(searchExerciseCatalog("b")).toEqual([]);
  });

  it("matches by exercise name, case-insensitively", () => {
    const results = searchExerciseCatalog("bench press");
    expect(results.some((r) => r.name === "Bench Press")).toBe(true);
  });

  it("matches by category label too", () => {
    const results = searchExerciseCatalog("deadlift");
    expect(results.length).toBeGreaterThan(0);
    expect(results.every((r) => r.categoryLabel === "Deadlift" || /deadlift/i.test(r.name))).toBe(
      true,
    );
  });
});

describe("entriesToApiSteps", () => {
  it("builds a reps-based exercise step with weight", () => {
    const entry = { ...emptyExerciseEntry(), durationType: "reps" as const, durationReps: "10" };
    entry.exerciseCategory = "BENCH_PRESS";
    entry.exerciseName = "";
    entry.weightKg = "60";

    const steps = entriesToApiSteps([entry], "");
    expect(steps).toEqual([
      {
        step_index: 0,
        duration_type: "reps",
        duration_reps: 10,
        duration_time_s: null,
        intensity: "active",
        exercise_category: "BENCH_PRESS",
        exercise_name: "",
        weight_kg: 60,
      },
    ]);
  });

  it("builds a rest step with no exercise fields", () => {
    const entry = emptyRestEntry();
    entry.durationTimeS = "90";

    const steps = entriesToApiSteps([entry], "");
    expect(steps).toEqual([
      { step_index: 0, duration_type: "time", duration_time_s: 90, intensity: "rest" },
    ]);
  });

  it("appends a repeat marker when repeatCount is a real count", () => {
    const entry = { ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" };
    const steps = entriesToApiSteps([entry], "3");

    expect(steps).toHaveLength(2);
    expect(steps[1]).toEqual({
      step_index: 1,
      duration_type: "repeat_until_steps_cmplt",
      repeat_from_step: 0,
      repeat_count: 3,
    });
  });

  it("omits the repeat marker for a count of 1 or an empty/invalid value", () => {
    const entry = { ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" };
    expect(entriesToApiSteps([entry], "1")).toHaveLength(1);
    expect(entriesToApiSteps([entry], "")).toHaveLength(1);
    expect(entriesToApiSteps([entry], "abc")).toHaveLength(1);
  });
});

describe("apiStepsToEntries", () => {
  it("round-trips a reps-based step with weight", () => {
    const steps = [
      step({
        duration_type: "reps",
        duration_reps: 8,
        intensity: "active",
        exercise_category: "BENCH_PRESS",
        exercise_name: "",
        weight_kg: 55,
      }),
    ];
    const { entries, repeatCount } = apiStepsToEntries(steps);
    expect(entries).toHaveLength(1);
    expect(entries[0].kind).toBe("exercise");
    expect(entries[0].durationType).toBe("reps");
    expect(entries[0].durationReps).toBe("8");
    expect(entries[0].weightKg).toBe("55");
    expect(entries[0].exerciseQuery).toBe("Bench Press");
    expect(repeatCount).toBe("");
  });

  it("recognizes a rest step by intensity", () => {
    const steps = [step({ duration_type: "time", duration_time_s: 60, intensity: "rest" })];
    const { entries } = apiStepsToEntries(steps);
    expect(entries[0].kind).toBe("rest");
    expect(entries[0].durationTimeS).toBe("60");
  });

  it("consumes a trailing repeat marker into repeatCount, not its own entry", () => {
    const steps = [
      step({
        step_index: 0,
        duration_type: "reps",
        duration_reps: 12,
        intensity: "active",
        exercise_category: "TOTAL_BODY",
        exercise_name: "BURPEE",
      }),
      step({
        step_index: 1,
        duration_type: "repeat_until_steps_cmplt",
        repeat_from_step: 0,
        repeat_count: 4,
      }),
    ];
    const { entries, repeatCount } = apiStepsToEntries(steps);
    expect(entries).toHaveLength(1);
    expect(repeatCount).toBe("4");
  });
});

describe("estimateExerciseDurationS", () => {
  it("uses the assumed seconds/rep for a reps-based step", () => {
    const entry = { ...emptyExerciseEntry(), durationType: "reps" as const, durationReps: "10" };
    expect(estimateExerciseDurationS([entry], "")).toBe(30); // 10 reps * 3s/rep
  });

  it("uses the real seconds for a time-based or rest step", () => {
    const exercise = { ...emptyExerciseEntry(), durationType: "time" as const, durationTimeS: "45" };
    const rest = emptyRestEntry();
    rest.durationTimeS = "60";
    expect(estimateExerciseDurationS([exercise, rest], "")).toBe(105);
  });

  it("multiplies by the repeat count", () => {
    const entry = { ...emptyExerciseEntry(), durationType: "time" as const, durationTimeS: "30" };
    expect(estimateExerciseDurationS([entry], "3")).toBe(90);
  });
});
