// The pre-planned workout structure recorded into some activities' own FIT files (Garmin
// Connect's "Workout" builder) -- a nested Warmup/5x[interval, recovery]/Cooldown list, matching
// how Garmin Connect itself presents it. Renders nothing when the activity has no such plan
// (the large majority don't) or the sport isn't a pace one (a target speed range only reads
// naturally as a pace -- see workoutSteps.ts::targetPaceRangeLabel).
import type { ActivityWorkoutOut } from "../api/types";
import { isPaceSport } from "../runningStats";
import {
  estimatedStepDistanceM,
  formatStepDistanceKm,
  formatStepDurationLabel,
  groupWorkoutStepsForDisplay,
  targetPaceRangeLabel,
} from "../workoutSteps";

export function WorkoutPlan({ workout, sport }: { workout: ActivityWorkoutOut; sport: string }) {
  if (!isPaceSport(sport) || workout.steps.length === 0) return null;

  const groups = groupWorkoutStepsForDisplay(workout.steps);

  return (
    <section className="card activity-workout-plan">
      <h2>{workout.name ?? "Workout plan"}</h2>
      {groups.map((group, i) => (
        <div className="activity-workout-plan__group" key={i}>
          <h4>{group.label}</h4>
          <ul>
            {group.steps.map((step) => {
              const duration = formatStepDurationLabel(step);
              const paceRange = targetPaceRangeLabel(step, sport);
              const estDistanceM = estimatedStepDistanceM(step);
              return (
                <li key={step.step_index}>
                  {duration}
                  {paceRange && ` ${paceRange}`}
                  {estDistanceM != null && ` (${formatStepDistanceKm(estDistanceM)})`}
                  {paceRange && " Pace"}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}
