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
import type { ActivityWorkoutStepOut } from "./api/types";
import { formatMinPerKm, isPaceSport, streamSpeedValue } from "./runningStats";

function sortedBySteps(steps: ActivityWorkoutStepOut[]): ActivityWorkoutStepOut[] {
  return [...steps].sort((a, b) => a.step_index - b.step_index);
}

/** Step indices absorbed into some repeat block -- computed as its own pass since a repeat
 * step's children appear *before* it in step_index order, so "is this consumed" can't be
 * determined while walking forward in a single pass. */
function consumedByRepeats(sorted: ActivityWorkoutStepOut[]): Set<number> {
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
export function expandWorkoutSteps(steps: ActivityWorkoutStepOut[]): ActivityWorkoutStepOut[] {
  const sorted = sortedBySteps(steps);
  const consumed = consumedByRepeats(sorted);
  const expanded: ActivityWorkoutStepOut[] = [];
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

export interface WorkoutDisplayGroup {
  // "Warmup" / "Cooldown" / "5x" / a capitalized intensity -- always a real label, never blank,
  // so the text panel never renders an unheaded bullet.
  label: string;
  // Set only for a repeat group -- distinguishes "5x" (a real repeat count) from a plain
  // single-step group sharing the same rendering otherwise.
  repeatCount: number | null;
  steps: ActivityWorkoutStepOut[];
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
export function groupWorkoutStepsForDisplay(steps: ActivityWorkoutStepOut[]): WorkoutDisplayGroup[] {
  const sorted = sortedBySteps(steps);
  const consumed = consumedByRepeats(sorted);
  const groups: WorkoutDisplayGroup[] = [];
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
      groups.push({ label: `${step.repeat_count}x`, repeatCount: step.repeat_count, steps: children });
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
export function targetPaceRangeLabel(
  step: ActivityWorkoutStepOut,
  sport: string,
): string | null {
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

/** "15m" / "75s" -- Garmin's own convention, confirmed against real figures: an exact multiple
 * of 60s is shown in minutes (900s -> "15m", 600s -> "10m"), anything else in raw seconds (75s
 * stays "75s", not "1m15s" or "1.25m"). */
export function formatStepDurationLabel(step: ActivityWorkoutStepOut): string | null {
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
