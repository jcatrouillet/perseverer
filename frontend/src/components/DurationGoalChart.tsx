// One duration goal's progress, in hours -- see GoalLineChart.tsx for the shared drawing.
import type { DurationGoalProgressOut } from "../api/types";
import { formatDurationHM } from "../runningStats";
import { GoalLineChart, periodStartIso } from "./GoalLineChart";

const HOUR = 3600;

export function DurationGoalChart({ progress }: { progress: DurationGoalProgressOut }) {
  const { goal } = progress;
  return (
    <GoalLineChart
      periodStart={periodStartIso(goal.period_type, goal.period_start)}
      periodEnd={progress.period_end}
      target={goal.target_duration_s / HOUR}
      targetPerDay={progress.target_per_day_s / HOUR}
      daily={progress.daily.map((d) => ({
        date: d.local_date,
        value: d.cumulative_duration_s / HOUR,
      }))}
      format={(hours) => formatDurationHM(hours * HOUR)}
      valueLabel="Time"
    />
  );
}
