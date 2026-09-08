import { beforeAll, describe, expect, it } from "vitest";

import type { PlannedWorkoutStepOut } from "../api/types";
import {
  apiStepsToItems,
  emptyExerciseEntry,
  emptyGroup,
  emptyRestEntry,
  estimateItemsDurationS,
  itemsToApiSteps,
  preloadExerciseCatalog,
  searchExerciseCatalog,
  type ExerciseEntry,
  type ExerciseGroup,
  type ExerciseItem,
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
    comment: null,
    ...overrides,
  };
}

function entryItem(entry: ExerciseEntry): ExerciseItem {
  return { type: "entry", entry };
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

  it("ranks an exact name match first even when far more entries match only by category label", () => {
    // "Squat" -- the bare category with no specific variant (Garmin's own
    // connect.garmin.com/app/exercises/SQUAT/SQUAT) -- must not get buried behind the ~100 other
    // SQUAT-category entries that also match this query only via categoryLabel.
    const results = searchExerciseCatalog("squat");
    expect(results[0]).toMatchObject({ name: "Squat", category: "SQUAT" });
  });
});

describe("itemsToApiSteps", () => {
  it("builds a reps-based exercise step with weight", () => {
    const entry = { ...emptyExerciseEntry(), durationType: "reps" as const, durationReps: "10" };
    entry.exerciseCategory = "BENCH_PRESS";
    entry.exerciseName = "";
    entry.weightKg = "60";

    const steps = itemsToApiSteps([entryItem(entry)]);
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
        comment: null,
      },
    ]);
  });

  it("builds a rest step with no exercise fields", () => {
    const entry = emptyRestEntry();
    entry.durationTimeS = "90";

    const steps = itemsToApiSteps([entryItem(entry)]);
    expect(steps).toEqual([
      {
        step_index: 0,
        duration_type: "time",
        duration_time_s: 90,
        intensity: "rest",
        comment: null,
      },
    ]);
  });

  it("appends a repeat marker after a set's own children when repeatCount is a real count", () => {
    const entry = { ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" };
    const group = { ...emptyGroup(), repeatCount: "3", entries: [entry] };

    const steps = itemsToApiSteps([{ type: "group", group }]);
    expect(steps).toHaveLength(2);
    expect(steps[1]).toEqual({
      step_index: 1,
      duration_type: "repeat_until_steps_cmplt",
      repeat_from_step: 0,
      repeat_count: 3,
      comment: null,
    });
  });

  it("omits the repeat marker for a count of 1 or an empty/invalid value", () => {
    const entry = { ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" };
    expect(itemsToApiSteps([{ type: "group", group: { ...emptyGroup(), repeatCount: "1", entries: [entry] } }])).toHaveLength(1);
    expect(itemsToApiSteps([{ type: "group", group: { ...emptyGroup(), repeatCount: "", entries: [entry] } }])).toHaveLength(1);
    expect(itemsToApiSteps([{ type: "group", group: { ...emptyGroup(), repeatCount: "abc", entries: [entry] } }])).toHaveLength(1);
  });

  it("builds a set of several exercises with rest, repeated together", () => {
    const squat = { ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" };
    const pushUp = { ...emptyExerciseEntry(), exerciseCategory: "PUSH_UP", exerciseName: "" };
    const rest = emptyRestEntry();
    const group = { ...emptyGroup(), repeatCount: "4", entries: [squat, pushUp, rest] };

    const steps = itemsToApiSteps([{ type: "group", group }]);
    expect(steps).toHaveLength(4); // 3 exercises/rest + 1 marker
    expect(steps.map((s) => s.duration_type)).toEqual([
      "reps",
      "reps",
      "time",
      "repeat_until_steps_cmplt",
    ]);
    expect(steps[3]).toEqual({
      step_index: 3,
      duration_type: "repeat_until_steps_cmplt",
      repeat_from_step: 0,
      repeat_count: 4,
      comment: null,
    });
  });

  it("supports standalone exercises alongside one or more sets, in order", () => {
    const warmup = { ...emptyExerciseEntry(), exerciseCategory: "WARM_UP", exerciseName: "" };
    const groupA = {
      ...emptyGroup(),
      repeatCount: "3",
      entries: [{ ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" }],
    };
    const groupB = {
      ...emptyGroup(),
      repeatCount: "2",
      entries: [{ ...emptyExerciseEntry(), exerciseCategory: "LUNGE", exerciseName: "" }],
    };

    const steps = itemsToApiSteps([
      entryItem(warmup),
      { type: "group", group: groupA },
      { type: "group", group: groupB },
    ]);

    // warmup(0), squat(1), markerA(2, repeat_from=1), lunge(3), markerB(4, repeat_from=3)
    expect(steps).toHaveLength(5);
    expect(steps[0].exercise_category).toBe("WARM_UP");
    expect(steps[1].exercise_category).toBe("SQUAT");
    expect(steps[2]).toMatchObject({
      duration_type: "repeat_until_steps_cmplt",
      repeat_from_step: 1,
      repeat_count: 3,
    });
    expect(steps[3].exercise_category).toBe("LUNGE");
    expect(steps[4]).toMatchObject({
      duration_type: "repeat_until_steps_cmplt",
      repeat_from_step: 3,
      repeat_count: 2,
    });
  });

  it("includes a typed comment on an entry", () => {
    const entry = { ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" };
    entry.comment = "Full depth";
    const steps = itemsToApiSteps([entryItem(entry)]);
    expect(steps[0].comment).toBe("Full depth");
  });

  it("includes a typed comment on a set's own repeat marker", () => {
    const entry = { ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" };
    const group = { ...emptyGroup(), repeatCount: "3", comment: "superset", entries: [entry] };
    const steps = itemsToApiSteps([{ type: "group", group }]);
    expect(steps[1].comment).toBe("superset");
  });

  it("a set's comment is lost when repeatCount stays at 1 (no marker row emitted)", () => {
    const entry = { ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" };
    const group = { ...emptyGroup(), repeatCount: "1", comment: "lost", entries: [entry] };
    const steps = itemsToApiSteps([{ type: "group", group }]);
    expect(steps).toHaveLength(1);
    expect(steps[0].comment).toBe(null); // the entry's own comment, not the group's
  });
});

describe("apiStepsToItems", () => {
  it("round-trips a standalone reps-based step with weight", () => {
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
    const items = apiStepsToItems(steps);
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe("entry");
    const entry = (items[0] as { type: "entry"; entry: ExerciseEntry }).entry;
    expect(entry.kind).toBe("exercise");
    expect(entry.durationType).toBe("reps");
    expect(entry.durationReps).toBe("8");
    expect(entry.weightKg).toBe("55");
    expect(entry.exerciseQuery).toBe("Bench Press");
  });

  it("recognizes a rest step by intensity", () => {
    const steps = [step({ duration_type: "time", duration_time_s: 60, intensity: "rest" })];
    const items = apiStepsToItems(steps);
    const entry = (items[0] as { type: "entry"; entry: ExerciseEntry }).entry;
    expect(entry.kind).toBe("rest");
    expect(entry.durationTimeS).toBe("60");
  });

  it("hydrates an entry's own comment", () => {
    const steps = [
      step({
        duration_type: "reps",
        duration_reps: 10,
        intensity: "active",
        exercise_category: "SQUAT",
        exercise_name: "",
        comment: "Full depth",
      }),
    ];
    const items = apiStepsToItems(steps);
    const entry = (items[0] as { type: "entry"; entry: ExerciseEntry }).entry;
    expect(entry.comment).toBe("Full depth");
  });

  it("hydrates a group's own comment from its repeat marker", () => {
    const steps = [
      step({
        step_index: 0,
        duration_type: "reps",
        intensity: "active",
        exercise_category: "SQUAT",
        exercise_name: "",
      }),
      step({
        step_index: 1,
        duration_type: "repeat_until_steps_cmplt",
        repeat_from_step: 0,
        repeat_count: 3,
        comment: "superset",
      }),
    ];
    const items = apiStepsToItems(steps);
    expect(items[0].type).toBe("group");
    const group = (items[0] as { type: "group"; group: ExerciseGroup }).group;
    expect(group.comment).toBe("superset");
  });

  it("consumes a trailing repeat marker into one group item, not its own entry", () => {
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
    const items = apiStepsToItems(steps);
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe("group");
    const group = (items[0] as { type: "group"; group: { repeatCount: string; entries: ExerciseEntry[] } }).group;
    expect(group.repeatCount).toBe("4");
    expect(group.entries).toHaveLength(1);
  });

  it("round-trips a standalone exercise followed by two separate sets", () => {
    const steps = itemsToApiSteps([
      entryItem({ ...emptyExerciseEntry(), exerciseCategory: "WARM_UP", exerciseName: "" }),
      {
        type: "group",
        group: {
          ...emptyGroup(),
          repeatCount: "3",
          entries: [{ ...emptyExerciseEntry(), exerciseCategory: "SQUAT", exerciseName: "" }],
        },
      },
      {
        type: "group",
        group: {
          ...emptyGroup(),
          repeatCount: "2",
          entries: [{ ...emptyExerciseEntry(), exerciseCategory: "LUNGE", exerciseName: "" }],
        },
      },
    ]);
    // Simulate the server round-trip shape (PlannedWorkoutStepOut carries the same fields plus
    // the ones PlannedWorkoutStepIn doesn't set, e.g. duration_distance_m).
    const asOut: PlannedWorkoutStepOut[] = steps.map((s) => step(s));

    const items = apiStepsToItems(asOut);
    expect(items.map((it) => it.type)).toEqual(["entry", "group", "group"]);
    const groupA = (items[1] as { type: "group"; group: { repeatCount: string; entries: ExerciseEntry[] } }).group;
    const groupB = (items[2] as { type: "group"; group: { repeatCount: string; entries: ExerciseEntry[] } }).group;
    expect(groupA.repeatCount).toBe("3");
    expect(groupA.entries[0].exerciseCategory).toBe("SQUAT");
    expect(groupB.repeatCount).toBe("2");
    expect(groupB.entries[0].exerciseCategory).toBe("LUNGE");
  });
});

describe("estimateItemsDurationS", () => {
  it("uses the assumed seconds/rep for a reps-based standalone exercise", () => {
    const entry = { ...emptyExerciseEntry(), durationType: "reps" as const, durationReps: "10" };
    expect(estimateItemsDurationS([entryItem(entry)])).toBe(30); // 10 reps * 3s/rep
  });

  it("uses the real seconds for a time-based or rest step", () => {
    const exercise = { ...emptyExerciseEntry(), durationType: "time" as const, durationTimeS: "45" };
    const rest = emptyRestEntry();
    rest.durationTimeS = "60";
    expect(estimateItemsDurationS([entryItem(exercise), entryItem(rest)])).toBe(105);
  });

  it("multiplies a set's own duration by its own repeat count", () => {
    const entry = { ...emptyExerciseEntry(), durationType: "time" as const, durationTimeS: "30" };
    const group = { ...emptyGroup(), repeatCount: "3", entries: [entry] };
    expect(estimateItemsDurationS([{ type: "group", group }])).toBe(90);
  });

  it("sums a standalone exercise plus multiple independently-repeated sets", () => {
    const warmup = { ...emptyExerciseEntry(), durationType: "time" as const, durationTimeS: "60" };
    const groupA = {
      ...emptyGroup(),
      repeatCount: "3",
      entries: [{ ...emptyExerciseEntry(), durationType: "time" as const, durationTimeS: "20" }],
    };
    const groupB = {
      ...emptyGroup(),
      repeatCount: "2",
      entries: [{ ...emptyExerciseEntry(), durationType: "time" as const, durationTimeS: "10" }],
    };
    // 60 + (20*3) + (10*2) = 60 + 60 + 20 = 140
    expect(
      estimateItemsDurationS([
        entryItem(warmup),
        { type: "group", group: groupA },
        { type: "group", group: groupB },
      ]),
    ).toBe(140);
  });
});
