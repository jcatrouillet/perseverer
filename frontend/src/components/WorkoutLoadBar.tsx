// A scheduled RUNNING workout's own distance/duration/load summary plus a Garmin-Connect-style
// load bar -- one colored segment per (already repeat-expanded) step, width proportional to its
// share of total duration, colored by the effort zone `planned_workout_stats.py` assigned it.
// Running only: yoga/bouldering/hiit/strength_training have no pace/HR targets to build any of
// this from, so `workout.segments` is always empty for them (api/routers/planned_workouts.py's
// own sport-gating) and this component renders nothing.
import type { PlannedWorkoutOut } from "../api/types";
import { toneColor, type Tone } from "../metricStyle";
import { ZONE_TONES } from "./TimeInZoneChart";
import "../styles/plannedWorkout.css";

function zoneColor(zone: number | null): string {
  if (zone == null) return toneColor("neutral");
  // Zones are 1-5; ZONE_TONES' first 5 entries are already the same cool-to-hot progression
  // TimeInZoneChart.tsx uses for HR zones elsewhere in the app.
  return toneColor(ZONE_TONES[zone - 1] as Tone);
}

export function WorkoutLoadBar({ workout }: { workout: PlannedWorkoutOut }) {
  if (workout.sport !== "running" || workout.segments.length === 0) return null;

  const totalDurationS = workout.segments.reduce((sum, s) => sum + s.duration_s, 0) || 1;

  return (
    <div className="workout-load">
      {workout.estimated_distance_m != null && workout.estimated_duration_s != null && (
        <p className="chart-note">
          Distance: {(workout.estimated_distance_m / 1000).toFixed(1)}km - Duration:{" "}
          {Math.round(workout.estimated_duration_s / 60)} minutes
        </p>
      )}
      {workout.estimated_load != null ? (
        <p className="workout-load__label">Load {Math.round(workout.estimated_load)}</p>
      ) : (
        <p className="chart-note">Configure a threshold pace in Settings for a load estimate.</p>
      )}
      <div className="workout-load-bar">
        {workout.segments.map((segment, i) => (
          <span
            key={i}
            className="workout-load-bar__segment"
            style={{
              width: `${(segment.duration_s / totalDurationS) * 100}%`,
              background: zoneColor(segment.zone),
            }}
          />
        ))}
      </div>
    </div>
  );
}
