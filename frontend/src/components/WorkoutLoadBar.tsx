// A scheduled RUNNING workout's own distance/duration/load summary plus a Garmin-Connect-style
// load bar -- one segment per (already repeat-expanded) step, width proportional to its share of
// total duration, colored by the discrete effort zone `planned_workout_stats.py` assigned it AND
// sized (height) from that same step's own continuous intensity_factor -- matching the athlete's
// own reference screenshots, where a hard interval reads as both a hotter color and a taller
// column than the easy work around it, not color alone. Height deliberately isn't just a lookup
// off the discrete zone number: two steps can share a zone bucket (see segmentHeightPx below)
// while still being visibly different paces, and only the continuous value tells them apart.
// Running only: yoga/bouldering/hiit/strength_training have no pace/HR targets to build any of
// this from, so `workout.segments` is always empty for them (api/routers/planned_workouts.py's
// own sport-gating) and this component renders nothing.
import type { PlannedWorkoutOut } from "../api/types";
import { toneColor } from "../metricStyle";
import "../styles/plannedWorkout.css";

// A dedicated 5-rung effort gradient (--color-zone-1..5, theme.css), NOT a reuse of
// TimeInZoneChart's ZONE_TONES: this bar packs many adjacent, often-narrow segments, and two
// neighboring-but-merely-different metric tones (e.g. cadence-teal next to elevation-green) read
// as one indistinguishable blob at that width. A real zone often repeats across many segments in
// a row too (a warmup/cooldown/recovery block, or several reps of the same interval), so it's
// the *between-zone* contrast that has to carry the workout's actual pace variation, not just a
// palette that happens to have 5 distinct entries.
const ZONE_COLOR_VARS = [
  "var(--color-zone-1)",
  "var(--color-zone-2)",
  "var(--color-zone-3)",
  "var(--color-zone-4)",
  "var(--color-zone-5)",
];

// Column height is driven by the segment's own CONTINUOUS intensity_factor, not its discrete
// zone number -- two steps can share one zone bucket (a fast-threshold athlete's 5:10-5:30/km
// and 4:50-5:15/km reps both land in zone 2) while still being a real, visible pace difference;
// bucketing height by zone alone made those two intervals draw identically. A rough absolute
// scale across real running efforts (an easy jog's IF sits around 0.65-0.75, a hard interval
// 1.05-1.15+) rather than a per-workout min/max stretch -- so the same pace always draws the same
// height regardless of what else is in this particular workout, and a genuinely uniform-pace
// workout doesn't get artificially stretched into looking varied.
const MIN_INTENSITY_FACTOR = 0.65;
const MAX_INTENSITY_FACTOR = 1.15;
const NEUTRAL_HEIGHT_PX = 8;
const BAR_HEIGHT_PX = 56;

function zoneColor(zone: number | null): string {
  if (zone == null) return toneColor("neutral");
  return ZONE_COLOR_VARS[zone - 1] ?? toneColor("neutral");
}

function segmentHeightPx(intensityFactor: number | null): number {
  if (intensityFactor == null) return NEUTRAL_HEIGHT_PX;
  const clamped = Math.min(
    Math.max(intensityFactor, MIN_INTENSITY_FACTOR),
    MAX_INTENSITY_FACTOR,
  );
  const t = (clamped - MIN_INTENSITY_FACTOR) / (MAX_INTENSITY_FACTOR - MIN_INTENSITY_FACTOR);
  return NEUTRAL_HEIGHT_PX + t * (BAR_HEIGHT_PX - NEUTRAL_HEIGHT_PX);
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
      <div className="workout-load-bar" style={{ height: BAR_HEIGHT_PX }}>
        {workout.segments.map((segment, i) => (
          <span
            key={i}
            className="workout-load-bar__segment"
            style={{
              width: `${(segment.duration_s / totalDurationS) * 100}%`,
              height: segmentHeightPx(segment.intensity_factor),
              background: zoneColor(segment.zone),
            }}
          />
        ))}
      </div>
    </div>
  );
}
