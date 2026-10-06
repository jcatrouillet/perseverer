// CTL (Fitness) and ATL (Fatigue) share one axis; TSB (Form) gets its own axis since its range
// doesn't share scale with CTL/ATL, plus a zero-line -- see
// docs/adr/0009-phase-6-calendar-rollups-fitness-health.md. Built on Recharts (ADR 0010): a
// dual-axis composed chart is exactly the shape Recharts is meant for, replacing the earlier
// hand-rolled-SVG version (ADR 0008) now that this app has a real charting need beyond one
// simple line.
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { FitnessDailyRollupOut } from "../api/types";
import { toneColor } from "../metricStyle";
import { ChartLegend } from "./ChartLegend";

// Same hues as before, now named in the shared tone vocabulary rather than reached for
// directly -- so these three series can't drift away from the tiles that describe them.
const CTL_COLOR = toneColor("elevation");
const ATL_COLOR = toneColor("load");
const TSB_COLOR = toneColor("pace");

/** Marks only the most recent point. On a chart whose whole question is "where am I right
 * now", the last value deserves to be findable without reading along the line to its end. */
function latestPointDot(lastIndex: number, color: string) {
  return function LatestDot(props: { cx?: number; cy?: number; index?: number }) {
    if (props.index !== lastIndex || props.cx == null || props.cy == null) return <g />;
    return (
      <circle
        className="chart-endpoint"
        cx={props.cx}
        cy={props.cy}
        r={4}
        fill={color}
        stroke="var(--color-surface)"
        strokeWidth={2}
      />
    );
  };
}

export function FitnessChart({ series }: { series: FitnessDailyRollupOut[] }) {
  if (series.length < 2) {
    return <p>Not enough data to chart Fitness &amp; Form yet.</p>;
  }

  const latest = series[series.length - 1]!;
  const lastIndex = series.length - 1;

  return (
    <div>
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={series} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          {/* Fitness is the baseline the other two are read against, so it gets a filled area
              to sit as the chart's ground rather than a third competing line. */}
          <defs>
            <linearGradient id="fitness-ctl-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CTL_COLOR} stopOpacity={0.28} />
              <stop offset="100%" stopColor={CTL_COLOR} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis dataKey="local_date" stroke="var(--color-text-muted)" fontSize={12} />
          <YAxis yAxisId="load" stroke="var(--color-text-muted)" fontSize={12} />
          <YAxis yAxisId="tsb" orientation="right" stroke="var(--color-text-muted)" fontSize={12} />
          <ReferenceLine
            yAxisId="tsb"
            y={0}
            stroke="var(--color-text-faint)"
            strokeDasharray="4 4"
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface-raised)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
            }}
          />
          <Area
            isAnimationActive={false}
            yAxisId="load"
            type="monotone"
            dataKey="ctl"
            name="Fitness (CTL)"
            stroke={CTL_COLOR}
            fill="url(#fitness-ctl-fill)"
            dot={latestPointDot(lastIndex, CTL_COLOR)}
            activeDot={{ r: 4 }}
            strokeWidth={2}
          />
          <Line
            isAnimationActive={false}
            yAxisId="load"
            type="monotone"
            dataKey="atl"
            name="Fatigue (ATL)"
            stroke={ATL_COLOR}
            dot={latestPointDot(lastIndex, ATL_COLOR)}
            strokeWidth={1.5}
            strokeDasharray="3 3"
          />
          <Line
            isAnimationActive={false}
            yAxisId="tsb"
            type="monotone"
            dataKey="tsb"
            name="Form (TSB)"
            stroke={TSB_COLOR}
            dot={latestPointDot(lastIndex, TSB_COLOR)}
            strokeWidth={2}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <ChartLegend
        items={[
          { label: "Fitness (CTL)", color: CTL_COLOR },
          { label: "Fatigue (ATL)", color: ATL_COLOR },
          { label: "Form (TSB)", color: TSB_COLOR },
        ]}
      />
      <p className="chart-note">
        Latest ({latest.local_date}): Fitness (CTL) {latest.ctl.toFixed(1)} · Fatigue (ATL){" "}
        {latest.atl.toFixed(1)} · Form (TSB) {latest.tsb.toFixed(1)}
      </p>
    </div>
  );
}
