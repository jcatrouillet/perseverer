// One bouldering goal's progress, in routes -- see GoalLineChart.tsx for the shared drawing.
import type { BoulderingGoalProgressOut } from "../api/types";
import { GoalLineChart, periodStartIso } from "./GoalLineChart";

function routes(n: number): string {
  const rounded = Math.round(n * 10) / 10;
  return `${rounded} route${rounded === 1 ? "" : "s"}`;
}

export function BoulderingGoalChart({ progress }: { progress: BoulderingGoalProgressOut }) {
  const { goal } = progress;
  return (
    <GoalLineChart
      periodStart={periodStartIso(goal.period_type, goal.period_start)}
      periodEnd={progress.period_end}
      target={goal.target_count}
      targetPerDay={progress.target_per_day}
      daily={progress.daily.map((d) => ({ date: d.local_date, value: d.cumulative_count }))}
      format={routes}
      valueLabel="Completed"
      wholeNumbers
      stepped
    />
  );
}
