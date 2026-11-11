// The logic behind the handwritten-notes editor of a HIIT / strength activity: turning what Claude
// read into editable drafts, matching an exercise written in the athlete's words to the exercise
// library, ordering the written exercises to the order the watch recorded them, and flattening the
// confirmed logbook back into sets for the muscles-worked card. Pure, so it is tested without the
// page.
import type { DecodedLogbook, ExerciseSetOut, LoggedExercise, PhotoOut } from "./api/types";
import type { ExerciseLibraryEntry } from "./exerciseLibrary";

const LB_TO_KG = 0.45359237;

export interface DraftSet {
  reps: number | null;
  weight_kg: number | null;
  duration_s: number | null;
}

export interface ExerciseDraft {
  /** Stable id for list keys while editing and reordering. */
  key: string;
  label: string;
  exercise_category: string | null;
  exercise_name: string | null;
  note: string | null;
  photo_id: number | null;
  sets: DraftSet[];
}

let nextKey = 0;
export function newDraftKey(): string {
  nextKey += 1;
  return `ex-${nextKey}`;
}

export function emptyDraft(): ExerciseDraft {
  return {
    key: newDraftKey(),
    label: "",
    exercise_category: null,
    exercise_name: null,
    note: null,
    photo_id: null,
    sets: [{ reps: null, weight_kg: null, duration_s: null }],
  };
}

/** What was read from a photo, as editable drafts, in the written order. Weights in pounds are
 * converted to kg (SI storage); a weight written with no unit is taken as kg. */
export function draftsFromDecoded(decoded: DecodedLogbook, photoId: number): ExerciseDraft[] {
  return decoded.exercises.map((exercise) => ({
    key: newDraftKey(),
    label: exercise.name,
    exercise_category: null,
    exercise_name: null,
    note: exercise.note,
    photo_id: photoId,
    sets: exercise.sets.map((s) => ({
      reps: s.reps,
      weight_kg:
        s.weight == null ? null : Math.round(s.weight * (s.unit === "lb" ? LB_TO_KG : 1) * 10) / 10,
      duration_s: s.duration_s,
    })),
  }));
}

export function draftsFromLogbook(exercises: LoggedExercise[]): ExerciseDraft[] {
  return exercises.map((e) => ({
    key: newDraftKey(),
    label: e.label,
    exercise_category: e.exercise_category,
    exercise_name: e.exercise_name,
    note: e.note,
    photo_id: e.photo_id,
    sets: e.sets.map((s) => ({ reps: s.reps, weight_kg: s.weight_kg, duration_s: s.duration_s })),
  }));
}

/** The first photo that has been read, as the starting point of the editor. */
export function firstReadPhoto(photos: PhotoOut[]): PhotoOut | null {
  return photos.find((p) => p.decode_status === "done" && p.decoded?.exercises.length) ?? null;
}

// --- Matching a written name to the exercise library ---------------------------------------------

const SYNONYMS: Record<string, string> = {
  db: "dumbbell",
  dbs: "dumbbell",
  dumbbells: "dumbbell",
  bb: "barbell",
  barbells: "barbell",
  kb: "kettlebell",
  kbs: "kettlebell",
  kettlebells: "kettlebell",
  bw: "bodyweight",
  ohp: "overhead press",
  rdl: "romanian deadlift",
  pullups: "pull up",
  pullup: "pull up",
  chinups: "chin up",
  pushups: "push up",
  pushup: "push up",
  situps: "sit up",
  squats: "squat",
  lunges: "lunge",
  curls: "curl",
  rows: "row",
  presses: "press",
  raises: "raise",
  crunches: "crunch",
  planks: "plank",
  dips: "dip",
};

function tokens(text: string): string[] {
  const words = text
    .toLowerCase()
    .replace(/[^a-z0-9 ]+/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  return words.flatMap((w) => (SYNONYMS[w] ?? w).split(" "));
}

/** 0..1: how well a library name covers what was written (Dice overlap of the words). */
export function nameScore(written: string, libraryName: string): number {
  const a = new Set(tokens(written));
  const b = new Set(tokens(libraryName));
  if (a.size === 0 || b.size === 0) return 0;
  let shared = 0;
  for (const w of a) if (b.has(w)) shared += 1;
  return (2 * shared) / (a.size + b.size);
}

export interface LibraryMatch {
  entry: ExerciseLibraryEntry;
  score: number;
}

/** The library exercises closest to what was written, best first. */
export function matchLibrary(
  written: string,
  library: ExerciseLibraryEntry[],
  limit = 6,
): LibraryMatch[] {
  if (tokens(written).length === 0) return [];
  return library
    .map((entry) => ({ entry, score: nameScore(written, entry.name) }))
    .filter((m) => m.score > 0)
    .sort((a, b) => b.score - a.score || a.entry.name.length - b.entry.name.length)
    .slice(0, limit);
}

/** A match good enough to apply without asking: the words written are the exercise's own. */
export const AUTO_MATCH_SCORE = 0.8;

// --- Ordering to the watch ---------------------------------------------------------------------

export interface RecordedBlock {
  category: string | null;
  name: string | null;
  sets: number;
  reps: (number | null)[];
}

/** The watch's working sets grouped into exercises: consecutive sets of one exercise category. */
export function recordedBlocks(sets: ExerciseSetOut[]): RecordedBlock[] {
  const blocks: RecordedBlock[] = [];
  for (const s of sets) {
    if (s.set_type === "REST") continue;
    const last = blocks[blocks.length - 1];
    if (last && last.category === s.exercise_category && last.category !== null) {
      last.sets += 1;
      last.reps.push(s.reps);
    } else {
      blocks.push({
        category: s.exercise_category,
        name: s.exercise_name,
        sets: 1,
        reps: [s.reps],
      });
    }
  }
  return blocks;
}

/** How well a written exercise looks like a recorded block: the same Garmin category counts most,
 * then the same number of sets, then the same repetitions. 0..1. */
export function blockScore(draft: ExerciseDraft, block: RecordedBlock): number {
  const category =
    draft.exercise_category !== null && draft.exercise_category === block.category ? 0.5 : 0;
  const setCount = draft.sets.length;
  const countScore =
    setCount === 0 || block.sets === 0
      ? 0
      : 0.3 * (1 - Math.abs(setCount - block.sets) / Math.max(setCount, block.sets));
  const written = draft.sets.map((s) => s.reps);
  const compared = Math.min(written.length, block.reps.length);
  let same = 0;
  for (let i = 0; i < compared; i += 1) {
    if (written[i] !== null && written[i] === block.reps[i]) same += 1;
  }
  const repsScore = compared === 0 ? 0 : 0.2 * (same / Math.max(written.length, block.reps.length));
  return category + countScore + repsScore;
}

export interface OrderResult {
  drafts: ExerciseDraft[];
  /** For each draft in the new order: the recorded block it was paired with, or null. */
  pairs: (number | null)[];
}

/** Reorders the written exercises to the order the watch recorded them. Each recorded block takes
 * the written exercise that looks most like it (best pairs first); exercises with no counterpart
 * keep their written order after the paired ones. A suggestion: the athlete reorders by hand. */
export function orderByRecorded(drafts: ExerciseDraft[], blocks: RecordedBlock[]): OrderResult {
  const pairs: { draft: number; block: number; score: number }[] = [];
  drafts.forEach((d, i) =>
    blocks.forEach((b, j) => {
      const score = blockScore(d, b);
      if (score > 0.25) pairs.push({ draft: i, block: j, score });
    }),
  );
  pairs.sort((a, b) => b.score - a.score || a.block - b.block || a.draft - b.draft);
  const blockOfDraft = new Map<number, number>();
  const taken = new Set<number>();
  for (const p of pairs) {
    if (blockOfDraft.has(p.draft) || taken.has(p.block)) continue;
    blockOfDraft.set(p.draft, p.block);
    taken.add(p.block);
  }
  const indices = drafts.map((_, i) => i);
  const paired = indices
    .filter((i) => blockOfDraft.has(i))
    .sort((a, b) => blockOfDraft.get(a)! - blockOfDraft.get(b)!);
  const rest = indices.filter((i) => !blockOfDraft.has(i));
  const order = [...paired, ...rest];
  return {
    drafts: order.map((i) => drafts[i]!),
    pairs: order.map((i) => blockOfDraft.get(i) ?? null),
  };
}

export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (to < 0 || to >= items.length || from === to) return items;
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved!);
  return next;
}

// --- Back to sets ------------------------------------------------------------------------------

/** The confirmed logbook as sets in the shape of the watch's, so the muscles-worked card reads
 * either the same way. */
export function logbookAsSets(exercises: LoggedExercise[]): ExerciseSetOut[] {
  let index = 0;
  return exercises.flatMap((e) =>
    e.sets.map((s) => ({
      set_index: index++,
      set_type: "ACTIVE",
      start_time_utc: null,
      duration_s: s.duration_s,
      reps: s.reps,
      weight_kg: s.weight_kg,
      exercise_category: e.exercise_category,
      exercise_name: e.exercise_name,
    })),
  );
}

export function draftsToPayload(drafts: ExerciseDraft[]): { exercises: LoggedExercise[] } {
  return {
    exercises: drafts
      .filter((d) => d.label.trim() !== "" && d.sets.length > 0)
      .map((d) => ({
        label: d.label.trim(),
        exercise_category: d.exercise_category,
        exercise_name: d.exercise_name,
        note: d.note,
        photo_id: d.photo_id,
        sets: d.sets,
      })),
  };
}
