import { describe, expect, it } from "vitest";

import type { DecodedLogbook, ExerciseSetOut } from "./api/types";
import type { ExerciseLibraryEntry } from "./exerciseLibrary";
import {
  draftsFromDecoded,
  draftsToPayload,
  logbookAsSets,
  matchLibrary,
  moveItem,
  nameScore,
  orderByRecorded,
  recordedBlocks,
  type ExerciseDraft,
} from "./logbookMatch";

function entry(name: string, category = "X"): ExerciseLibraryEntry {
  return {
    name,
    category,
    categoryLabel: category,
    exercise: name.toUpperCase().replace(/ /g, "_"),
    garmin_url: null,
    primary_muscles: [],
    secondary_muscles: [],
    tier: 3,
    image_url: null,
    image_source: null,
    description: null,
    difficulty: null,
  };
}

function draft(label: string, category: string | null, reps: number[]): ExerciseDraft {
  return {
    key: label,
    label,
    exercise_category: category,
    exercise_name: null,
    note: null,
    photo_id: null,
    sets: reps.map((r) => ({ reps: r, weight_kg: null, duration_s: null })),
  };
}

function watchSet(category: string | null, reps: number | null, type = "ACTIVE"): ExerciseSetOut {
  return {
    set_index: 0,
    set_type: type,
    start_time_utc: null,
    duration_s: null,
    reps,
    weight_kg: null,
    exercise_category: category,
    exercise_name: null,
  };
}

describe("reading a photo into drafts", () => {
  const decoded: DecodedLogbook = {
    legible: true,
    transcript: "",
    exercises: [
      {
        name: "Bench",
        note: null,
        sets: [
          { reps: 10, weight: 100, unit: "lb", duration_s: null },
          { reps: 8, weight: 20, unit: null, duration_s: null },
          { reps: null, weight: null, unit: null, duration_s: 45 },
        ],
      },
    ],
  };

  it("converts pounds to kg, keeps unitless weights as kg and leaves gaps null", () => {
    const [bench] = draftsFromDecoded(decoded, 7);
    expect(bench!.photo_id).toBe(7);
    expect(bench!.sets.map((s) => s.weight_kg)).toEqual([45.4, 20, null]);
    expect(bench!.sets[2]).toMatchObject({ reps: null, duration_s: 45 });
  });
});

describe("matching a written name to the library", () => {
  const library = [
    entry("Dumbbell Bench Press"),
    entry("Barbell Bench Press"),
    entry("Dumbbell Curl"),
    entry("Pull Up"),
  ];

  it("expands the shorthand lifters write", () => {
    const top = matchLibrary("DB bench press", library)[0]!;
    expect(top.entry.name).toBe("Dumbbell Bench Press");
    expect(top.score).toBeGreaterThan(0.8);
    expect(matchLibrary("pullups", library)[0]!.entry.name).toBe("Pull Up");
  });

  it("returns nothing for text with no overlap and scores identical names 1", () => {
    expect(matchLibrary("zzz", library)).toEqual([]);
    expect(nameScore("Pull Up", "pull-up")).toBe(1);
  });
});

describe("ordering the written exercises to the watch", () => {
  it("groups the watch's working sets into exercises and skips rests", () => {
    const blocks = recordedBlocks([
      watchSet("SQUAT", 10),
      watchSet("SQUAT", 10),
      watchSet(null, null, "REST"),
      watchSet("CURL", 12),
    ]);
    expect(blocks.map((b) => [b.category, b.sets])).toEqual([
      ["SQUAT", 2],
      ["CURL", 1],
    ]);
  });

  it("puts each written exercise where the watch recorded its look-alike", () => {
    // Written: curl, then squat. Done: squat (2 x 10), then curl (3 x 12).
    const written = [draft("Curl", "CURL", [12, 12, 12]), draft("Squat", "SQUAT", [10, 10])];
    const blocks = recordedBlocks([
      watchSet("SQUAT", 10),
      watchSet("SQUAT", 10),
      watchSet("CURL", 12),
      watchSet("CURL", 12),
      watchSet("CURL", 12),
    ]);
    const result = orderByRecorded(written, blocks);
    expect(result.drafts.map((d) => d.label)).toEqual(["Squat", "Curl"]);
    expect(result.pairs).toEqual([0, 1]);
  });

  it("uses set counts and reps when the exercise is not matched to the library", () => {
    const written = [draft("A", null, [8, 8, 8, 8]), draft("B", null, [15, 15])];
    const blocks = recordedBlocks([
      watchSet("UNKNOWN_1", 15),
      watchSet("UNKNOWN_1", 15),
      watchSet("UNKNOWN_2", 8),
      watchSet("UNKNOWN_2", 8),
      watchSet("UNKNOWN_2", 8),
      watchSet("UNKNOWN_2", 8),
    ]);
    expect(orderByRecorded(written, blocks).drafts.map((d) => d.label)).toEqual(["B", "A"]);
  });

  it("keeps unpaired exercises after the paired ones, in their written order", () => {
    const written = [draft("X", null, [1]), draft("Squat", "SQUAT", [10]), draft("Y", null, [2])];
    const result = orderByRecorded(written, recordedBlocks([watchSet("SQUAT", 10)]));
    expect(result.drafts.map((d) => d.label)).toEqual(["Squat", "X", "Y"]);
    expect(result.pairs).toEqual([0, null, null]);
  });

  it("moves an item and ignores an out-of-range move", () => {
    expect(moveItem(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
    const same = ["a", "b"];
    expect(moveItem(same, 0, -1)).toBe(same);
  });
});

describe("back to sets", () => {
  it("drops blank exercises and flattens the rest in order", () => {
    const drafts = [
      draft("Squat", "SQUAT", [10, 10]),
      draft("", null, [5]),
      draft("Curl", null, []),
    ];
    const payload = draftsToPayload(drafts);
    expect(payload.exercises.map((e) => e.label)).toEqual(["Squat"]);
    const sets = logbookAsSets(payload.exercises);
    expect(sets.map((s) => [s.set_index, s.exercise_category, s.reps])).toEqual([
      [0, "SQUAT", 10],
      [1, "SQUAT", 10],
    ]);
  });
});
