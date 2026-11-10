// Which muscles a HIIT / strength activity worked, and how hard, deduced from its recorded sets:
// each set names a Garmin exercise, the exercise library says which muscles it works (primary and
// secondary), and the sets add up per muscle. Pure, so it is tested without the page.
import type { ExerciseSetOut } from "./api/types";
import type { ExerciseLibraryEntry } from "./exerciseLibrary";

/** A secondary muscle works less than a primary one: a set counts for half. */
export const SECONDARY_WEIGHT = 0.5;

export interface MuscleLoad {
  muscle: string;
  /** Weighted sets: 1 per set for a primary muscle, 0.5 for a secondary one. */
  sets: number;
  /** 0..1 against the hardest-worked muscle of this activity. */
  intensity: number;
}

export interface MusclesWorked {
  muscles: MuscleLoad[];
  /** Working sets counted (rest sets are not). */
  workingSets: number;
  /** Working sets whose exercise is not in the library, so no muscle could be credited. */
  unmatchedSets: number;
}

function exerciseKey(category: string, name: string): string {
  return `${category}|${name}`;
}

/** An active set is one that was lifted; Garmin marks the pauses between them as REST. */
function isWorkingSet(set: ExerciseSetOut): boolean {
  return set.set_type !== "REST";
}

export function musclesWorked(
  sets: ExerciseSetOut[],
  library: ExerciseLibraryEntry[],
): MusclesWorked {
  const byExercise = new Map<string, ExerciseLibraryEntry>();
  for (const entry of library) byExercise.set(exerciseKey(entry.category, entry.exercise), entry);

  const totals = new Map<string, number>();
  let workingSets = 0;
  let unmatchedSets = 0;
  for (const set of sets) {
    if (!isWorkingSet(set)) continue;
    workingSets += 1;
    const entry =
      set.exercise_category && set.exercise_name
        ? byExercise.get(exerciseKey(set.exercise_category, set.exercise_name))
        : undefined;
    const primary = entry?.primary_muscles.filter(Boolean) ?? [];
    const secondary = entry?.secondary_muscles.filter(Boolean) ?? [];
    if (primary.length === 0 && secondary.length === 0) {
      unmatchedSets += 1;
      continue;
    }
    for (const muscle of primary) totals.set(muscle, (totals.get(muscle) ?? 0) + 1);
    for (const muscle of secondary) {
      if (primary.includes(muscle)) continue;
      totals.set(muscle, (totals.get(muscle) ?? 0) + SECONDARY_WEIGHT);
    }
  }

  const max = Math.max(0, ...totals.values());
  const muscles = [...totals.entries()]
    .map(([muscle, count]) => ({ muscle, sets: count, intensity: max > 0 ? count / max : 0 }))
    .sort((a, b) => b.sets - a.sets || a.muscle.localeCompare(b.muscle));
  return { muscles, workingSets, unmatchedSets };
}
