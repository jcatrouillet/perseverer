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

export function HealthTrendChart({
  metrics,
  keys,
}: {
  metrics: HealthDashboardMetricOut[];
  keys: string[];
}) {
  const data = mergeTrendSeries(metrics, keys);
  // A logical metric can exist in `metrics` (it has data *somewhere* in history) yet contribute
  // zero points to this date range -- e.g. the underlying raw export stopped including that
  // field. Filtering on actual points in `data`, not just presence in `metrics`, keeps an
  // empty line from silently occupying a legend swatch as if it had data.
  const present = keys.filter((k) => data.some((point) => point[k] != null));
  const missing = keys.filter((k) => !present.includes(k));

  if (data.length === 0) {
    return <p>No data for this section in the selected range.</p>;
  }

  return (
    <div>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="local_date"
            stroke="var(--color-text-muted)"
            fontSize={11}
            tickFormatter={(iso: string) => iso.slice(5)}
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
      {missing.length > 0 && (
        <p className="chart-note">
          No data in this range for: {missing.map((k) => k.replace(/_/g, " ")).join(", ")}
        </p>
      )}
    </div>
  );
}
