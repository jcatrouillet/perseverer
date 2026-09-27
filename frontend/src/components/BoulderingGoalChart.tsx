// One bouldering goal's progress: a straight grey target-pace line across the whole period against
// the athlete's actual cumulative count of completed matching routes up to today -- the same
// target-vs-actual picture GoalProgressChart.tsx draws for a distance goal, in routes instead of
// kilometres (a count, so whole-number ticks and no unit conversion).
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

import type { BoulderingGoalProgressOut } from "../api/types";
import "../styles/goals.css";

const GOLD = "var(--color-load)";
const GREY = "var(--color-text-faint)";

interface Point {
  ts: number;
  count: number;
  series: "Actual" | "Target";
  targetCount?: number;
  diff?: number;
}

function parseIsoUtc(iso: string): number {
  return new Date(`${iso}T00:00:00Z`).getTime();
}

function formatTick(ts: number): string {
  return new Date(ts).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function routes(n: number): string {
  return `${n} route${n === 1 ? "" : "s"}`;
}

/** First day of the goal's period as an ISO date. */
export function periodStartIso(periodType: string, periodStart: string): string {
  if (periodType === "year") return `${periodStart}-01-01`;
  if (periodType === "month") return `${periodStart}-01`;
  return periodStart;
}

// Exported for direct unit testing (real Recharts hover needs a layout engine jsdom lacks).
export function BoulderingGoalTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload || payload.length === 0) return null;
  const actual = payload
    .map((p) => p.payload as Point | undefined)
    .find((p) => p?.series === "Actual");
  if (!actual || actual.targetCount == null || actual.diff == null) return null;
  const ahead = actual.diff >= 0;
  return (
    <div className="goal-progress__tooltip">
      <div className="goal-progress__tooltip-date">
        {new Date(actual.ts).toLocaleDateString(undefined, {
          month: "long",
          day: "numeric",
          year: "numeric",
          timeZone: "UTC",
        })}
      </div>
      <div>Target: {actual.targetCount.toFixed(1)}</div>
      <div>Completed: {routes(actual.count)}</div>
      <div className={ahead ? "goal-progress__tooltip-ahead" : "goal-progress__tooltip-behind"}>
        {ahead ? "Ahead" : "Behind"}: {ahead ? "+" : "-"}
        {Math.abs(actual.diff).toFixed(1)}
      </div>
    </div>
  );
}

export function BoulderingGoalChart({ progress }: { progress: BoulderingGoalProgressOut }) {
  const { goal } = progress;
  const startTs = parseIsoUtc(periodStartIso(goal.period_type, goal.period_start));
  const endTs = parseIsoUtc(progress.period_end);

  const targetData: Point[] = [
    { ts: startTs, count: 0, series: "Target" },
    { ts: endTs, count: goal.target_count, series: "Target" },
  ];
  // progress.daily has exactly one entry per calendar day from the period start (gap-filled with
  // no new routes), so index i + 1 is the days elapsed -- the same identity the backend's own
  // target_as_of_today is computed from, so this per-day target never drifts from the summary.
  const actualData: Point[] = progress.daily.map((p, i) => {
    const targetCount = progress.target_per_day * (i + 1);
    return {
      ts: parseIsoUtc(p.local_date),
      count: p.cumulative_count,
      series: "Actual",
      targetCount,
      diff: p.cumulative_count - targetCount,
    };
  });
  const maxCount = Math.max(goal.target_count, actualData[actualData.length - 1]?.count ?? 0);

  return (
    <div className="goal-progress">
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart margin={{ top: 8, right: 24, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={[startTs, endTs]}
            stroke="var(--color-text-muted)"
            fontSize={11}
            tickFormatter={formatTick}
          />
          <YAxis
            dataKey="count"
            type="number"
            domain={[0, Math.ceil(maxCount * 1.05)]}
            allowDecimals={false}
            stroke="var(--color-text-muted)"
            fontSize={11}
            width={36}
          />
          <Tooltip content={(props) => <BoulderingGoalTooltip {...props} />} />
          <Line
            data={targetData}
            dataKey="count"
            stroke={GREY}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            legendType="none"
          />
          <Line
            data={actualData}
            dataKey="count"
            type="stepAfter"
            stroke={GOLD}
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
            legendType="none"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
