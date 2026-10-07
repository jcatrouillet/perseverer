// Pure transforms over a pre-planned workout's raw, unexpanded step list (see
// routers/activities.py::get_activity_workout's own docstring for why it's stored/served
// unexpanded: a "repeat_until_steps_cmplt" step describes "repeat steps [repeat_from_step..
// step_index-1] repeat_count times", not pre-flattened repetitions). Two different shapes are
// needed from the same raw list:
//   - `expandWorkoutSteps`: the actual executed sequence, one entry per repetition -- used to
//     align with recorded laps (the device creates one lap per executed step, confirmed against
//     a real structured-workout FIT file: 12 expanded steps, 13 recorded laps, the last being
//     the device's own auto-generated "stop" lap).
//   - `groupWorkoutStepsForDisplay`: a nested Warmup/5x[interval, recovery]/Cooldown grouping
//     for a human-readable panel -- matching how Garmin Connect itself presents a workout, not
//     a flat expanded list.
import type { ActivityWorkoutStepOut, PlannedWorkoutStepOut } from "./api/types";
import { formatMinPerKm, isPaceSport, streamSpeedValue } from "./runningStats";

// Generalized over the two step shapes this app has: ActivityWorkoutStepOut (recorded, parsed
// from a device's own FIT workout_mesgs, target_low_mps/target_high_mps, speed-only) and
// PlannedWorkoutStepOut (authored on the calendar, workout_syntax.py, target_low/target_high in
// pace-or-HR units plus target_hr_zone/cadence). Both share the same repeat-block/step_index/
// intensity shape (deliberately -- see db/schema.py::planned_workout_step's own docstring), so
// expand/group need only this common subset, not either concrete type -- letting the planned-
// workout schedule form reuse the exact same grouping/expansion this file already had for
// recorded activities, per docs/ARCHITECTURE.md.
interface WorkoutStepLike {
  step_index: number;
  duration_type: string | null;
  intensity: string | null;
  repeat_from_step: number | null;
  repeat_count: number | null;
}

function sortedBySteps<T extends WorkoutStepLike>(steps: T[]): T[] {
  return [...steps].sort((a, b) => a.step_index - b.step_index);
}

/** Step indices absorbed into some repeat block -- computed as its own pass since a repeat
 * step's children appear *before* it in step_index order, so "is this consumed" can't be
 * determined while walking forward in a single pass. */
function consumedByRepeats<T extends WorkoutStepLike>(sorted: T[]): Set<number> {
  const consumed = new Set<number>();
  for (const step of sorted) {
    if (
      step.duration_type === "repeat_until_steps_cmplt" &&
      step.repeat_from_step != null &&
      step.repeat_count != null
    ) {
      for (let i = step.repeat_from_step; i < step.step_index; i++) consumed.add(i);
    }
  }
  return consumed;
}

/** The actual executed sequence: every repeat block replaced by `repeat_count` copies of the
 * steps it repeats, in order. A step with no repeat wrapping it passes through unchanged. */
export function expandWorkoutSteps<T extends WorkoutStepLike>(steps: T[]): T[] {
  const sorted = sortedBySteps(steps);
  const consumed = consumedByRepeats(sorted);
  const expanded: T[] = [];
  for (const step of sorted) {
    if (consumed.has(step.step_index)) continue;
    if (
      step.duration_type === "repeat_until_steps_cmplt" &&
      step.repeat_from_step != null &&
      step.repeat_count != null
    ) {
      const children = sorted.filter(
        (s) => s.step_index >= step.repeat_from_step! && s.step_index < step.step_index,
      );
      for (let i = 0; i < step.repeat_count; i++) expanded.push(...children);
    } else {
      expanded.push(step);
    }
  }
  return expanded;
}

interface LapLike {
  duration_s: number | null;
  moving_duration_s: number | null;
  distance_m: number | null;
}

// A lap's own recorded duration/distance is deemed to have "completed" its matched step once it
// reaches this fraction of the step's own target -- generous enough to absorb ordinary GPS/timer
// noise on a genuine one-lap-per-step match, strict enough that a lap barely a third of the way
// into a step (the 6:10-of-10:00 example below) never triggers an early advance.
const STEP_COMPLETE_FRACTION = 0.9;

/** Maps each recorded lap to its own planned/recorded workout step, tolerant of a device
 * inserting an extra lap mid-step -- confirmed against a real activity where the watch's own
 * distance-based autolap setting (independent of the pushed workout's step boundaries) split a
 * planned 10-minute step into a 6:10 lap plus a 3:50 lap, which silently shifted every following
 * lap's positional index against `expandWorkoutSteps` by one (the Intervals table's "Expected"
 * columns, and ActivityCharts.tsx's workoutBands overlay, both used to assume `laps[i] <->
 * expandedSteps[i]` positionally -- see each call site's own comment before this function
 * existed). Greedily accumulates consecutive laps' duration/distance against the *current*
 * expected step's own target until `STEP_COMPLETE_FRACTION` of it is reached, only then advancing
 * to the next step -- so two (or more) laps that together complete one step are both correctly
 * matched to that same step, instead of the second one being matched to the step after it. A step
 * with no time/distance target to measure against (an open step, or `lap_button`, which by
 * definition ends exactly on a lap boundary already) always advances after exactly one lap,
 * preserving the original one-lap-per-step assumption for those. Returns `null` for a lap once
 * every expected step is accounted for -- the device's own trailing "stop" lap, or the athlete
 * continuing past the plan. */
export function alignLapsToWorkoutSteps<T extends WorkoutStepLike & DurationStepLike>(
  laps: LapLike[],
  expandedSteps: T[],
): (T | null)[] {
  const result: (T | null)[] = [];
  let stepIdx = 0;
  let accumulatedDuration = 0;
  let accumulatedDistance = 0;
  for (const lap of laps) {
    if (stepIdx >= expandedSteps.length) {
      result.push(null);
      continue;
    }
    const step = expandedSteps[stepIdx]!;
    result.push(step);
    accumulatedDuration += lap.moving_duration_s ?? lap.duration_s ?? 0;
    accumulatedDistance += lap.distance_m ?? 0;
    const reached =
      step.duration_type === "time" && step.duration_time_s != null
        ? accumulatedDuration >= step.duration_time_s * STEP_COMPLETE_FRACTION
        : step.duration_type === "distance" && step.duration_distance_m != null
          ? accumulatedDistance >= step.duration_distance_m * STEP_COMPLETE_FRACTION
          : true;
    if (reached) {
      stepIdx += 1;
      accumulatedDuration = 0;
      accumulatedDistance = 0;
    }
  }
  return result;
}

export interface WorkoutDisplayGroup<T extends WorkoutStepLike> {
  // "Warmup" / "Cooldown" / "5x" / a capitalized intensity -- always a real label, never blank,
  // so the text panel never renders an unheaded bullet.
  label: string;
  // Set only for a repeat group -- distinguishes "5x" (a real repeat count) from a plain
  // single-step group sharing the same rendering otherwise.
  repeatCount: number | null;
  steps: T[];
}

/** "Warmup" / "Active" / "Recovery" / "Cooldown" / "Rest" -- a step's own planned intensity,
 * capitalized. Exported for the Intervals table's own "Interval" column (per-lap), not just
 * `groupWorkoutStepsForDisplay`'s grouped panel. */
export function labelForIntensity(intensity: string | null): string {
  if (!intensity) return "Step";
  return intensity.charAt(0).toUpperCase() + intensity.slice(1);
}

/** The nested Warmup/5x[...]/Cooldown grouping for a human-readable panel -- a repeat block's
 * own children are rendered once, as the repeat group's `steps`, not flattened into the
 * top-level list (contrast `expandWorkoutSteps`, which does flatten, for chart alignment). */
export function groupWorkoutStepsForDisplay<T extends WorkoutStepLike>(
  steps: T[],
): WorkoutDisplayGroup<T>[] {
  const sorted = sortedBySteps(steps);
  const consumed = consumedByRepeats(sorted);
  const groups: WorkoutDisplayGroup<T>[] = [];
  for (const step of sorted) {
    if (consumed.has(step.step_index)) continue;
    if (
      step.duration_type === "repeat_until_steps_cmplt" &&
      step.repeat_from_step != null &&
      step.repeat_count != null
    ) {
      const children = sorted.filter(
        (s) => s.step_index >= step.repeat_from_step! && s.step_index < step.step_index,
      );
      groups.push({
        label: `${step.repeat_count}x`,
        repeatCount: step.repeat_count,
        steps: children,
      });
    } else {
      groups.push({ label: labelForIntensity(step.intensity), repeatCount: null, steps: [step] });
    }
  }
  return groups;
}

/** [fast, slow] min/km, faster (higher speed) bound first -- returns null for a non-pace sport
 * or a step with no speed target (see ActivityWorkoutStepOut's own docstring on the backend for
 * why only target_type == "speed" carries a range at all). Shared by `targetPaceRangeLabel`
 * (text panel) and the chart overlay, which needs the raw numbers rather than a formatted
 * string. */
export function targetPaceRangeMinPerKm(
  step: ActivityWorkoutStepOut,
  sport: string,
): [number, number] | null {
  if (
    !isPaceSport(sport) ||
    step.target_type !== "speed" ||
    step.target_low_mps == null ||
    step.target_high_mps == null
  ) {
    return null;
  }
  const fast = streamSpeedValue(sport, step.target_high_mps);
  const slow = streamSpeedValue(sport, step.target_low_mps);
  if (fast == null || slow == null) return null;
  return [fast, slow];
}

/** "6:25-6:50" -- faster (higher speed) pace first, matching how a target range naturally
 * reads. */
export function targetPaceRangeLabel(step: ActivityWorkoutStepOut, sport: string): string | null {
  const range = targetPaceRangeMinPerKm(step, sport);
  if (range == null) return null;
  return `${formatMinPerKm(range[0])}-${formatMinPerKm(range[1])}`;
}

/** Garmin Connect's own convention for a time-based step's displayed distance: duration x the
 * target range's *low* (slower) speed bound -- confirmed by back-computing from Garmin's own
 * displayed figures on a real workout (900s @ 2.439-2.597 m/s shows "2.2km", which only the low
 * bound reproduces: 900 x 2.439 = 2195m -> 2.2km; the midpoint speed gives 2266m -> 2.3km,
 * which is not what Garmin shows). An estimate either way (the athlete's actual pace during
 * that step determines the real distance), shown in parens alongside the duration for exactly
 * that reason. Distance-based steps already show their real target distance as the primary
 * duration figure and don't need this. */
export function estimatedStepDistanceM(step: ActivityWorkoutStepOut): number | null {
  if (
    step.duration_type !== "time" ||
    step.duration_time_s == null ||
    step.target_low_mps == null
  ) {
    return null;
  }
  return step.target_low_mps * step.duration_time_s;
}

/** "2.2km" / "0.18km" -- matches Garmin's own precision, confirmed against the same real
 * figures as `estimatedStepDistanceM`: 2 decimals under 1km (0.1786 -> "0.18"), 1 decimal at or
 * above (2.1951 -> "2.2", 1.4634 -> "1.5"). */
export function formatStepDistanceKm(meters: number): string {
  const km = meters / 1000;
  return km < 1 ? `${km.toFixed(2)}km` : `${km.toFixed(1)}km`;
}

interface DurationStepLike {
  duration_type: string | null;
  duration_time_s: number | null;
  duration_distance_m: number | null;
  // hiit/strength_training only ("reps" duration_type) -- absent from ActivityWorkoutStepOut
  // entirely, hence optional: a recorded step never has this, only an authored planned one.
  duration_reps?: number | null;
}

/** "15m" / "75s" / "10 reps" -- Garmin's own convention for time (confirmed against real
 * figures: an exact multiple of 60s is shown in minutes, 900s -> "15m", 600s -> "10m"; anything
 * else in raw seconds, 75s stays "75s", not "1m15s" or "1.25m"). Generalized (see
 * WorkoutStepLike above) so both the planned-workout schedule form's own live preview and its
 * read-only summary (WorkoutSummary, ScheduleWorkoutForm.tsx) reuse this rather than a second
 * duration formatter. */
export function formatStepDurationLabel(step: DurationStepLike): string | null {
  if (step.duration_type === "reps" && step.duration_reps != null) {
    return `${step.duration_reps} reps`;
  }
  if (step.duration_type === "lap_button") {
    // The step ends on the button, so the label leads with that; any duration/distance on the
    // step is only Perseverer's own estimate (see workout_syntax.py) and is shown as such rather
    // than as a duration the watch will actually advance on.
    if (step.duration_distance_m != null) {
      const km = step.duration_distance_m / 1000;
      return `Lap button (~${Number.isInteger(km) ? km : km.toFixed(2)}km)`;
    }
    if (step.duration_time_s != null) {
      // Same exact-minutes-or-raw-seconds convention the plain "time" branch below uses, not a
      // bespoke rounded-minutes-only format -- a "lap 90s" estimate should read "~90s", not the
      // less accurate "~2m".
      const label =
        step.duration_time_s % 60 === 0
          ? `${step.duration_time_s / 60}m`
          : `${Math.round(step.duration_time_s)}s`;
      return `Lap button (~${label})`;
    }
    return "Lap button";
  }
  if (step.duration_type === "distance" && step.duration_distance_m != null) {
    const km = step.duration_distance_m / 1000;
    return `${Number.isInteger(km) ? km : km.toFixed(2)}km`;
  }
  if (step.duration_type === "time" && step.duration_time_s != null) {
    return step.duration_time_s % 60 === 0
      ? `${step.duration_time_s / 60}m`
      : `${Math.round(step.duration_time_s)}s`;
  }
  return null;
}

// --- Planned-workout-specific target rendering -- PlannedWorkoutStepOut can target pace *or*
// heart rate (an absolute range or a "Z2"-style zone) *and* carry a cadence range alongside
// either, which ActivityWorkoutStepOut's speed-only target never could -- see
// db/schema.py::planned_workout_step's own docstring. Kept separate from targetPaceRangeLabel
// above rather than folded in: the two step shapes' target semantics genuinely differ (m/s vs.
// bpm vs. zone number), and one function trying to branch across both would obscure more than
// it'd share.

/** "5:00-5:10/km" / "5:10/km" (pace), "140-150 bpm" / "150 bpm" (absolute HR), or "Z2" (a zone)
 * -- whichever the step actually targets, or null for an open/no-target step. */
export function plannedTargetLabel(step: PlannedWorkoutStepOut): string | null {
  if (step.target_type === "pace" && step.target_low != null && step.target_high != null) {
    const fast = formatMinPerKm(1000 / step.target_high / 60);
    const slow = formatMinPerKm(1000 / step.target_low / 60);
    return fast === slow ? `${fast}/km` : `${fast}-${slow}/km`;
  }
  if (step.target_type === "heart_rate") {
    if (step.target_hr_zone != null) return `Z${step.target_hr_zone}`;
    if (step.target_low != null && step.target_high != null) {
      return step.target_low === step.target_high
        ? `${step.target_low} bpm`
        : `${step.target_low}-${step.target_high} bpm`;
    }
  }
  return null;
}

/** "170-180 spm" / "175 spm" -- null when the step has no cadence target at all. */
export function plannedCadenceLabel(step: PlannedWorkoutStepOut): string | null {
  if (step.cadence_low == null || step.cadence_high == null) return null;
  return step.cadence_low === step.cadence_high
    ? `${step.cadence_low} spm`
    : `${step.cadence_low}-${step.cadence_high} spm`;
}

function humanizeExerciseToken(token: string): string {
  return token
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/** "Bench Press" / "Push-up" / "Rest" / "Exercise" -- a hiit/strength_training step's own
 * exercise name, humanized from Garmin's own SCREAMING_SNAKE_CASE catalog tokens (prefers the
 * specific exercise_name over the bare exercise_category, same rule calendar_feed.py::
 * _step_line already uses for the calendar-feed description of these sports). Kept as an
 * independent frontend-only formatter rather than a port of that Python function -- this is a
 * read-only display concern with no live-typed-preview-vs-authoritative-parse split the way
 * workout_syntax.py/workoutSyntax.ts have, so there's nothing to keep in sync across languages. */
export function plannedExerciseLabel(step: PlannedWorkoutStepOut): string {
  if (step.exercise_name) return humanizeExerciseToken(step.exercise_name);
  if (step.exercise_category) return humanizeExerciseToken(step.exercise_category);
  if (step.intensity === "rest") return "Rest";
  return "Exercise";
}
