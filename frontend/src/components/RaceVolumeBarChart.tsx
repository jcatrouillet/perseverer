// Shared by Race Readiness' two dedicated volume charts (weekly distance, long run): one bar per
// Monday-start week's realized distance, plus a dashed ReferenceLine at the target -- same
// "bars against a threshold" idiom as EddingtonBarChart.tsx, and the same dashed-line styling
// TrendChart.tsx's own zero reference line uses, just at y=target instead of y=0.
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

import type { RaceReadinessWeekOut } from "../api/types";

function formatKm(m: number): string {
  return `${(m / 1000).toFixed(1)} km`;
}

function formatWeekLabel(weekStart: string): string {
  const d = new Date(`${weekStart}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

interface WeekBar extends RaceReadinessWeekOut {
  label: string;
}

function BarTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const week = payload[0]!.payload as WeekBar;
  return (
    <div
      style={{
        background: "var(--color-surface-raised)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 12px",
      }}
    >
      <p style={{ margin: 0, fontWeight: 600 }}>Week of {week.label}</p>
      <p style={{ margin: 0 }}>{formatKm(week.distance_m)}</p>
    </div>
  );
}

export function RaceVolumeBarChart({
  weeks,
  targetM,
  color,
}: {
  weeks: RaceReadinessWeekOut[];
  targetM: number | null;
  color: string;
}) {
  if (weeks.length === 0) return null;

  const data: WeekBar[] = weeks.map((w) => ({ ...w, label: formatWeekLabel(w.week_start) }));

  return (
    <ResponsiveContainer width="100%" height={240}>
      <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="label"
          stroke="var(--color-text-muted)"
          fontSize={11}
          interval="preserveStartEnd"
          minTickGap={24}
        />
        <YAxis
          dataKey="distance_m"
          stroke="var(--color-text-muted)"
          fontSize={11}
          width={48}
          domain={[0, "auto"]}
          tickFormatter={(v: number) => formatKm(v)}
        />
        <Tooltip content={BarTooltip} cursor={{ fill: "var(--color-surface-raised)" }} />
        {targetM != null && (
          <ReferenceLine y={targetM} stroke="var(--color-text-faint)" strokeDasharray="4 4" />
        )}
        <Bar dataKey="distance_m" fill={color} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
