import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { HealthDashboardMetricOut } from "../api/types";
import { ChartLegend } from "./ChartLegend";
import { mergeTrendSeries } from "../healthStats";
import { healthMetricStyle, toneColor } from "../metricStyle";

/** Each series takes the colour of the metric it plots, not of its position in the array --
 * so HRV is the same teal here as on its own tile, and adding a fourth metric can't silently
 * recolour the other three. */
function seriesColor(logicalMetric: string): string {
  return toneColor(healthMetricStyle(logicalMetric).tone);
}

const SHORT_MONTH_NAMES = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

export function formatAxisTick(ts: number, tickGranularity: "day" | "month" | "year"): string {
  const d = new Date(ts);
  if (tickGranularity === "year") {
    return String(d.getUTCFullYear());
  }
  if (tickGranularity === "month") {
    return `${SHORT_MONTH_NAMES[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
  return d.toISOString().slice(5, 10);
}

export function HealthTrendChart({
  metrics,
  keys,
  // Day-of-month ticks ("08-15") are meaningless once the visible range spans years (the
  // all-time body-composition charts) -- the caller opts into "Jan 2024"-style or plain-year
  // ticks instead.
  tickGranularity = "day",
}: {
  metrics: HealthDashboardMetricOut[];
  keys: string[];
  tickGranularity?: "day" | "month" | "year";
}) {
  const data = mergeTrendSeries(metrics, keys);
  // A logical metric can exist in `metrics` (it has data *somewhere* in history) yet contribute
  // zero points to this date range -- e.g. the underlying raw export stopped including that
  // field. Filtering on actual points in `data`, not just presence in `metrics`, keeps an
  // empty line from silently occupying a legend swatch as if it had data.
  const present = keys.filter((k) => data.some((point) => point[k] != null));

  // Nothing to plot at all -- hide the whole chart rather than showing an empty frame or a
  // "no data" placeholder (a summary view should only ever show what it actually has).
  if (data.length === 0 || present.length === 0) {
    return null;
  }

  // Recharts' own auto-tick placement for a numeric/time-scale axis picks ticks at a roughly
  // month-ish "nice" interval regardless of tickFormatter -- reformatting every one of those
  // ticks down to just its year (via formatAxisTick below) would print the same year many times
  // over ("2023" repeated for every month-tick that falls in 2023) rather than once. Explicit
  // `ticks` overrides that entirely: one tick per distinct calendar year actually present in the
  // data, positioned at that year's Jan 1.
  const yearTicks =
    tickGranularity === "year"
      ? Array.from(new Set(data.map((p) => new Date(p.ts).getUTCFullYear())))
          .sort((a, b) => a - b)
          .map((year) => Date.UTC(year, 0, 1))
      : undefined;

  return (
    <div>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          {/* A numeric time scale, not the default category axis -- a category axis spaces every
              point evenly regardless of the actual gap between them, which makes a week-long gap
              between two sparse Eufy readings (weight, body composition) look identical to two
              consecutive days. This makes the x-axis reflect real elapsed time instead. */}
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            ticks={yearTicks}
            stroke="var(--color-text-muted)"
            fontSize={11}
            tickFormatter={(ts: number) => formatAxisTick(ts, tickGranularity)}
          />
          {/* Recharts' own default domain is [0, "auto"], which forces every line down into a
              sliver at the top of the chart for a metric whose real range never goes near zero
              (resting heart rate, respiration, ...). "auto" on both ends instead zooms to the
              data's own range (with Recharts' usual padded/rounded tick bounds), the same way
              the pace-vs-distance scatter charts already zoom to their own data instead of
              starting at 0. */}
          <YAxis stroke="var(--color-text-muted)" fontSize={11} width={32} domain={["auto", "auto"]} />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface-raised)",
              border: "1px solid var(--color-border)",
            }}
            labelFormatter={(ts) =>
              typeof ts === "number" ? new Date(ts).toISOString().slice(0, 10) : ts
            }
            formatter={(value, name) => [
              typeof value === "number" ? value.toFixed(1) : value,
              String(name).replace(/_/g, " "),
            ]}
            // Recharts' own default tooltip order follows internal render/stacking state, not
            // the `keys` array order -- explicitly sorting by value (highest first) instead
            // keeps it predictable regardless of that, and reads naturally for series like
            // weight vs. muscle mass where one is always larger than the other.
            itemSorter={(item) => -(typeof item.value === "number" ? item.value : 0)}
          />
          {present.map((key) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={seriesColor(key)}
              dot={false}
              strokeWidth={2}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      {/* A legend only earns its space once there's more than one line to tell apart -- for a
          single series the chart's own heading already says what it is. */}
      {present.length > 1 && (
        <ChartLegend
          items={present.map((key) => ({
            label: key.replace(/_/g, " "),
            color: seriesColor(key),
          }))}
        />
      )}
    </div>
  );
}
