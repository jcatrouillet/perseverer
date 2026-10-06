// The classic VeloViewer-style Eddington bar chart: one bar per km threshold, height = how many
// of this year's runs reached at least that far. Green while the bar still clears its own
// threshold (count >= km, i.e. km <= this year's Eddington number), red once it doesn't -- the
// point where the bars drop below the dotted y=x diagonal is the Eddington number itself.
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

import type { EddingtonBar } from "../eddington";

function BarTooltip({ active, payload, unitLabel }: TooltipContentProps & { unitLabel: string }) {
  if (!active || !payload?.length) return null;
  const bar = payload[0]!.payload as EddingtonBar;
  return (
    <div
      style={{
        background: "var(--color-surface-raised)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 12px",
      }}
    >
      <p style={{ margin: 0, fontWeight: 600 }}>
        {bar.km} {unitLabel}
      </p>
      <p style={{ margin: 0 }}>
        {bar.count} run{bar.count === 1 ? "" : "s"} that far or further
      </p>
    </div>
  );
}

export function EddingtonBarChart({
  bars,
  unitLabel,
}: {
  bars: EddingtonBar[];
  unitLabel: string;
}) {
  if (bars.length === 0) return null;

  const maxKm = bars[bars.length - 1]!.km;
  const ticks: number[] = [];
  for (let km = 2; km <= maxKm; km += 2) ticks.push(km);

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={bars} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="km"
          type="number"
          domain={[0, maxKm]}
          ticks={ticks}
          stroke="var(--color-text-muted)"
          fontSize={11}
          unit={unitLabel}
        />
        <YAxis
          dataKey="count"
          type="number"
          domain={[0, (dataMax: number) => Math.ceil(dataMax)]}
          stroke="var(--color-text-muted)"
          fontSize={11}
          width={36}
        />
        <Tooltip
          content={(props) => <BarTooltip {...props} unitLabel={unitLabel} />}
          cursor={{ fill: "var(--color-surface-raised)" }}
        />
        <Bar dataKey="count" isAnimationActive={false}>
          {bars.map((bar) => (
            <Cell
              key={bar.km}
              fill={bar.count >= bar.km ? "var(--color-success)" : "var(--color-danger)"}
            />
          ))}
        </Bar>
        <Line
          dataKey="diagonal"
          stroke="var(--color-text-muted)"
          strokeWidth={1.5}
          strokeDasharray="1 4"
          strokeLinecap="round"
          dot={false}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
