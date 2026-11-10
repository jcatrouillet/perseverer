import { describe, expect, it } from "vitest";

import type { ExerciseSetOut } from "./api/types";
import type { ExerciseLibraryEntry } from "./exerciseLibrary";
import { musclesWorked } from "./musclesWorked";

function entry(
  category: string,
  exercise: string,
  primary: string[],
  secondary: string[],
): ExerciseLibraryEntry {
  return {
    name: exercise,
    category,
    categoryLabel: category,
    exercise,
    garmin_url: null,
    primary_muscles: primary,
    secondary_muscles: secondary,
    tier: 3,
    image_url: null,
    image_source: null,
    description: null,
    difficulty: null,
  };
}

function set(overrides: Partial<ExerciseSetOut>): ExerciseSetOut {
  return {
    set_index: 0,
    set_type: "ACTIVE",
    start_time_utc: null,
    duration_s: null,
    reps: 10,
    weight_kg: null,
    exercise_category: null,
    exercise_name: null,
    ...overrides,
  };
}

const LIBRARY = [
  entry("BENCH_PRESS", "BARBELL_BENCH_PRESS", ["Chest"], ["Triceps", "Shoulders"]),
  entry("CURL", "BARBELL_CURL", ["Biceps"], ["Forearm"]),
];

const bench = { exercise_category: "BENCH_PRESS", exercise_name: "BARBELL_BENCH_PRESS" };

describe("musclesWorked", () => {
  it("credits primary muscles a full set and secondary ones half, scaled to the hardest", () => {
    const result = musclesWorked(
      [
        set({ ...bench }),
        set({ ...bench, set_index: 1 }),
        set({ exercise_category: "CURL", exercise_name: "BARBELL_CURL", set_index: 2 }),
      ],
      LIBRARY,
    );
    expect(result.workingSets).toBe(3);
    const by = Object.fromEntries(result.muscles.map((m) => [m.muscle, m]));
    expect(by.Chest).toMatchObject({ sets: 2, intensity: 1 });
    expect(by.Triceps).toMatchObject({ sets: 1, intensity: 0.5 });
    expect(by.Biceps).toMatchObject({ sets: 1, intensity: 0.5 });
    expect(by.Forearm).toMatchObject({ sets: 0.5, intensity: 0.25 });
    expect(result.muscles[0]!.muscle).toBe("Chest");
  });

  it("ignores rest sets and counts sets of unknown exercises as unmatched", () => {
    const result = musclesWorked(
      [
        set({ ...bench }),
        set({ set_type: "REST", set_index: 1, ...bench }),
        set({ set_index: 2, exercise_category: "NOPE", exercise_name: "NOPE" }),
        set({ set_index: 3 }),
      ],
      LIBRARY,
    );
    expect(result.workingSets).toBe(3);
    expect(result.unmatchedSets).toBe(2);
    expect(result.muscles.map((m) => m.muscle)).toContain("Chest");
  });

  it("returns nothing worked when no set matches", () => {
    expect(musclesWorked([set({})], LIBRARY).muscles).toEqual([]);
    expect(musclesWorked([], LIBRARY)).toEqual({ muscles: [], workingSets: 0, unmatchedSets: 0 });
  });
});
