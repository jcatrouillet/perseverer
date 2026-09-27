// The target-vs-actual line chart shared by the bouldering (routes) and duration (hours) goals: a
// straight grey target-pace line across the whole period against the athlete's actual cumulative
// value up to today -- the same picture GoalProgressChart.tsx draws for a distance goal, made
// generic over the unit. Values arrive already in display units (routes, hours).
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

import "../styles/goals.css";

const GOLD = "var(--color-load)";
const GREY = "var(--color-text-faint)";

export interface GoalLineDay {
  /** ISO date. */
  date: string;
  /** Cumulative value up to and including this day, in display units. */
  value: number;
}

interface Point {
  ts: number;
  value: number;
  series: "Actual" | "Target";
  targetValue?: number;
  diff?: number;
}

export function parseIsoUtc(iso: string): number {
  return new Date(`${iso}T00:00:00Z`).getTime();
}

function formatTick(ts: number): string {
  return new Date(ts).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** First day of a goal's period as an ISO date. */
export function periodStartIso(periodType: string, periodStart: string): string {
  if (periodType === "year") return `${periodStart}-01-01`;
  if (periodType === "month") return `${periodStart}-01`;
  return periodStart;
}

function GoalLineTooltip({
  active,
  payload,
  format,
  valueLabel,
}: TooltipContentProps & { format: (n: number) => string; valueLabel: string }) {
  if (!active || !payload || payload.length === 0) return null;
  const actual = payload
    .map((p) => p.payload as Point | undefined)
    .find((p) => p?.series === "Actual");
  if (!actual || actual.targetValue == null || actual.diff == null) return null;
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
      <div>Target: {format(actual.targetValue)}</div>
      <div>
        {valueLabel}: {format(actual.value)}
      </div>
      <div className={ahead ? "goal-progress__tooltip-ahead" : "goal-progress__tooltip-behind"}>
        {ahead ? "Ahead" : "Behind"}: {ahead ? "+" : "-"}
        {format(Math.abs(actual.diff))}
      </div>
    </div>
  );
}

export function GoalLineChart({
  periodStart,
  periodEnd,
  target,
  targetPerDay,
  daily,
  format,
  valueLabel,
  wholeNumbers = false,
  stepped = false,
}: {
  /** ISO dates of the period's first and last day. */
  periodStart: string;
  periodEnd: string;
  target: number;
  /** Straight-line pace, in display units per day. */
  targetPerDay: number;
  /** One entry per calendar day from the period start through today. */
  daily: GoalLineDay[];
  /** Formats a value for the tooltip. */
  format: (n: number) => string;
  /** What the actual line is called in the tooltip ("Completed", "Time"). */
  valueLabel: string;
  /** Whole-number Y ticks -- a count of routes, not hours. */
  wholeNumbers?: boolean;
  /** Draw the actual line as steps -- for a count that jumps rather than accrues smoothly. */
  stepped?: boolean;
}) {
  const startTs = parseIsoUtc(periodStart);
  const endTs = parseIsoUtc(periodEnd);

  const targetData: Point[] = [
    { ts: startTs, value: 0, series: "Target" },
    { ts: endTs, value: target, series: "Target" },
  ];
  // `daily` has exactly one entry per calendar day from the period start (gap-filled), so index
  // i + 1 is the days elapsed -- the identity the backend's own target-as-of-today uses, so this
  // per-day target never drifts from the summary tile above the chart.
  const actualData: Point[] = daily.map((d, i) => {
    const targetValue = targetPerDay * (i + 1);
    return {
      ts: parseIsoUtc(d.date),
      value: d.value,
      series: "Actual",
      targetValue,
      diff: d.value - targetValue,
    };
  });
  const maxValue = Math.max(target, actualData[actualData.length - 1]?.value ?? 0);

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
            dataKey="value"
            type="number"
            domain={[0, Math.ceil(maxValue * 1.05)]}
            allowDecimals={!wholeNumbers}
            stroke="var(--color-text-muted)"
            fontSize={11}
            width={36}
          />
          <Tooltip
            content={(props) => (
              <GoalLineTooltip {...props} format={format} valueLabel={valueLabel} />
            )}
          />
          <Line
            data={targetData}
            dataKey="value"
            stroke={GREY}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            legendType="none"
          />
          <Line
            data={actualData}
            dataKey="value"
            type={stepped ? "stepAfter" : "linear"}
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
