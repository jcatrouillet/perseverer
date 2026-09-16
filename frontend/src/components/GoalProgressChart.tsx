// The big popup graph itself (see GoalButton.tsx for why this never renders inline on the
// calendar page): a straight grey target-pace line across the whole period against the actual
// gold cumulative-distance line up to today, styled after the reference SPI/Strava goal widget
// the user pointed at.
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

import type { GoalProgressOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import "../styles/goals.css";

const GOLD = "var(--color-load)";
const GREY = "var(--color-text-faint)";

function parseIsoUtc(iso: string): number {
  return new Date(`${iso}T00:00:00Z`).getTime();
}

function formatDateTick(ts: number): string {
  return new Date(ts).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

interface ChartPoint {
  ts: number;
  km: number;
}

interface TooltipRow {
  payload: ChartPoint & { seriesLabel: "Actual" | "Target" };
}

function isTooltipRow(entry: unknown): entry is TooltipRow {
  return (
    typeof entry === "object" &&
    entry != null &&
    "payload" in entry &&
    typeof (entry as { payload?: unknown }).payload === "object"
  );
}

function GoalTooltip({
  active,
  payload,
  unitLabel,
}: TooltipContentProps & { unitLabel: string }) {
  if (!active || !payload || payload.length === 0) return null;
  const rows = payload.filter(isTooltipRow);
  if (rows.length === 0) return null;
  const date = new Date(rows[0]!.payload.ts).toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
  return (
    <div className="goal-progress__tooltip">
      <div className="goal-progress__tooltip-date">{date}</div>
      {rows.map((row) => (
        <div key={row.payload.seriesLabel}>
          {row.payload.seriesLabel}: {row.payload.km.toFixed(1)} {unitLabel}
        </div>
      ))}
    </div>
  );
}

export function GoalProgressChart({ progress }: { progress: GoalProgressOut }) {
  const { metersToDisplay, unitLabel } = useDistanceFormat();
  if (!progress.available || progress.goal == null || progress.period_end == null) return null;

  const periodStartTs = parseIsoUtc(
    progress.goal.period_type === "year"
      ? `${progress.goal.period_start}-01-01`
      : `${progress.goal.period_start}-01`,
  );
  const periodEndTs = parseIsoUtc(progress.period_end);
  const targetKm = metersToDisplay(progress.goal.target_distance_m);

  const targetData: (ChartPoint & { seriesLabel: "Target" })[] = [
    { ts: periodStartTs, km: 0, seriesLabel: "Target" },
    { ts: periodEndTs, km: targetKm, seriesLabel: "Target" },
  ];
  const actualData: (ChartPoint & { seriesLabel: "Actual" })[] = progress.daily.map((p) => ({
    ts: parseIsoUtc(p.local_date),
    km: metersToDisplay(p.cumulative_distance_m),
    seriesLabel: "Actual",
  }));

  const maxKm = Math.max(targetKm, actualData[actualData.length - 1]?.km ?? 0);

  return (
    <div className="goal-progress">
      <ResponsiveContainer width="100%" height={680}>
        <ComposedChart margin={{ top: 8, right: 24, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={[periodStartTs, periodEndTs]}
            stroke="var(--color-text-muted)"
            fontSize={11}
            tickFormatter={formatDateTick}
          />
          <YAxis
            dataKey="km"
            type="number"
            domain={[0, Math.ceil(maxKm * 1.05)]}
            stroke="var(--color-text-muted)"
            fontSize={11}
            width={48}
            unit={` ${unitLabel}`}
          />
          <Tooltip content={(props) => <GoalTooltip {...props} unitLabel={unitLabel} />} />
          <Line
            data={targetData}
            dataKey="km"
            stroke={GREY}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            legendType="none"
          />
          <Line
            data={actualData}
            dataKey="km"
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
