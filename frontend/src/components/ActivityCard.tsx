// The per-activity snapshot card for the activity list and the day view (Milestone B/D of
// docs/adr/0010-phase-6.1-frontend-design.md's plan). Built entirely on the Milestone A design
// system -- sport icon/colour from metricStyle.ts's sportStyle(), numeric readouts as
// MetricChip pills in the metric's own tone -- rather than inventing a second visual language
// for "one activity" versus "one stat tile".
//
// Fields shown are exactly what GET /activities already returns: distance, duration, avg
// pace/speed, avg heart rate, training load, and workout RPE, each only when actually present.
// There is deliberately no "completion" or "execution" indicator -- that would need a planned-
// workout target to compare against, which this project has no data model for; showing one
// anyway would mean inventing a number, which is exactly what the raw-first ethos rules out.
import { Link } from "wouter";

import type { ActivitySummary } from "../api/types";
import { sportStyle } from "../metricStyle";
import {
  effectiveDurationS,
  formatDurationHM,
  formatPaceMinPerKm,
  isPaceSport,
  localTimeLabel,
} from "../runningStats";
import { displaySport } from "../yearStats";
import { Icon } from "./Icon";
import { MetricChip } from "./StatTile";

function paceOrSpeedLabel(sport: string, durationS: number, distanceM: number): string {
  if (isPaceSport(sport)) {
    return `${formatPaceMinPerKm(durationS, distanceM)} /km`;
  }
  const kmh = distanceM / 1000 / (durationS / 3600);
  return `${kmh.toFixed(1)} km/h`;
}

export function ActivityCard({ activity }: { activity: ActivitySummary }) {
  const sport = displaySport(activity);
  const style = sportStyle(sport);
  const durationS = effectiveDurationS(activity);

  return (
    <Link href={`/activities/${activity.id}`} className="activity-card">
      <div className="activity-card__header">
        <span className={`icon-chip tone-${style.tone}`}>
          <Icon name={style.icon} />
        </span>
        <div className="activity-card__title">
          <span className="activity-card__sport">{sport.replace(/_/g, " ")}</span>
          {activity.name && <span className="activity-card__name">{activity.name}</span>}
        </div>
        <span className="activity-card__time">{localTimeLabel(activity)}</span>
      </div>

      <div className="activity-card__stats">
        {activity.distance_m != null && (
          <MetricChip
            label={`${(activity.distance_m / 1000).toFixed(2)} km`}
            icon="route"
            tone="pace"
          />
        )}
        {durationS != null && (
          <MetricChip label={formatDurationHM(durationS)} icon="clock" tone="cadence" />
        )}
        {/* Distance > 0, not just non-null: a strength-training session logged with an exact
            0m distance is real data, but a "0.0 km/h" pace chip on it would read as a broken
            measurement rather than "this activity has no meaningful distance". */}
        {activity.distance_m != null && activity.distance_m > 0 && durationS != null && durationS > 0 && (
          <MetricChip
            label={paceOrSpeedLabel(sport, durationS, activity.distance_m)}
            icon="gauge"
            tone="pace"
          />
        )}
        {activity.avg_hr_bpm != null && (
          <MetricChip label={`${Math.round(activity.avg_hr_bpm)} bpm`} icon="heart" tone="hr" />
        )}
        {activity.training_load != null && (
          <MetricChip
            label={`Load ${Math.round(activity.training_load)}`}
            icon="bolt"
            tone="load"
          />
        )}
        {activity.workout_rpe != null && (
          <MetricChip
            label={`RPE ${activity.workout_rpe.toFixed(1)}`}
            icon="flame"
            tone="load"
          />
        )}
      </div>
    </Link>
  );
}
