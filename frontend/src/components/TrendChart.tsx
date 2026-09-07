// The one chart component behind every metric on the Fitness & Form and Health tabs -- a
// generic multi-series trend chart over whatever TrendPoint[] trendWindow.ts's
// bucketSeriesToWindow already produced. Deliberately not per-metric bespoke components (the
// way FitnessChart.tsx/HealthTrendChart.tsx are for the calendar views' own embedded cards):
// this file's whole job is looking identical across VO2max, HRV, weight, blood pressure, and
// everything else these two tabs show, per the user's own "same graph, same options" request.
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

import type { TrendPoint } from "../trendWindow";
import { ChartLegend } from "./ChartLegend";

export interface TrendSeries {
  key: string;
  label: string;
  color: string;
  /** Which Y axis this series reads against -- only needed when two series on one chart don't
   * share a scale (Fitness & Form's TSB vs CTL/ATL, Lactate threshold's pace vs heart rate).
   * Defaults to "left", the only axis drawn unless some series asks for "right". */
  axis?: "left" | "right";
  kind?: "line" | "area";
  formatValue?: (value: number) => string;
}

function defaultFormat(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function TrendChart({
  points,
  series,
  /** Draws a dashed zero reference line on this axis -- e.g. Fitness & Form's Form (TSB), which
   * is meaningfully positive/negative, unlike every other metric these tabs chart. */
  referenceZeroAxis,
  height = 240,
}: {
  points: TrendPoint[];
  series: TrendSeries[];
  referenceZeroAxis?: "left" | "right";
  height?: number;
}) {
  const present = series.filter((s) => points.some((p) => typeof p[s.key] === "number"));
  if (present.length === 0) {
    return null;
  }

  const needsRightAxis = present.some((s) => s.axis === "right");
  const latest = [...points].reverse().find((p) => present.some((s) => typeof p[s.key] === "number"));

  // Each axis's own tick labels use whichever series reads against it -- same formatter the
  // Tooltip and "latest" note already apply, so an axis of raw seconds (race predictions) or
  // pace (threshold/GAP charts) reads as "25:30"/"4:15 /km" on its ticks too, not a bare number
  // with no unit. When two series share an axis (rare) the first one's formatter wins, matching
  // how they'd already agree on the same scale to share an axis at all.
  const leftFormat = present.find((s) => (s.axis ?? "left") === "left")?.formatValue ?? defaultFormat;
  const rightFormat = present.find((s) => s.axis === "right")?.formatValue ?? defaultFormat;

  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis dataKey="x" stroke="var(--color-text-muted)" fontSize={11} />
          <YAxis
            yAxisId="left"
            stroke="var(--color-text-muted)"
            fontSize={11}
            width={48}
            domain={["auto", "auto"]}
            tickFormatter={leftFormat}
          />
          {needsRightAxis && (
            <YAxis
              yAxisId="right"
              orientation="right"
              stroke="var(--color-text-muted)"
              fontSize={11}
              width={48}
              domain={["auto", "auto"]}
              tickFormatter={rightFormat}
            />
          )}
          {referenceZeroAxis && (
            <ReferenceLine
              yAxisId={referenceZeroAxis}
              y={0}
              stroke="var(--color-text-faint)"
              strokeDasharray="4 4"
            />
          )}
          <Tooltip
            contentStyle={{
              background: "var(--color-surface-raised)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
            }}
            formatter={(value, name) => {
              const s = present.find((s) => s.label === name);
              if (typeof value !== "number") return [value, name];
              return [(s?.formatValue ?? defaultFormat)(value), name];
            }}
            itemSorter={(item) => -(typeof item.value === "number" ? item.value : 0)}
          />
          {present.map((s) =>
            s.kind === "area" ? (
              <Area
                key={s.key}
                isAnimationActive={false}
                yAxisId={s.axis ?? "left"}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={s.color}
                fill={s.color}
                fillOpacity={0.15}
                strokeWidth={2}
                connectNulls
                dot={false}
                activeDot={{ r: 4 }}
              />
            ) : (
              <Line
                key={s.key}
                isAnimationActive={false}
                yAxisId={s.axis ?? "left"}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={s.color}
                strokeWidth={2}
                connectNulls
                dot={{ r: 2, fill: s.color, strokeWidth: 0 }}
                activeDot={{ r: 4 }}
              />
            ),
          )}
        </ComposedChart>
      </ResponsiveContainer>
      {present.length > 1 && (
        <ChartLegend items={present.map((s) => ({ label: s.label, color: s.color }))} />
      )}
      {latest && (
        <p className="chart-note">
          {latest.x}:{" "}
          {present
            .filter((s) => typeof latest[s.key] === "number")
            .map((s) => `${s.label} ${(s.formatValue ?? defaultFormat)(latest[s.key] as number)}`)
            .join(" · ")}
        </p>
      )}
    </div>
  );
}
