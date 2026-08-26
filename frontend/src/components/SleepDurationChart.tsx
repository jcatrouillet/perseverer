// A small bar chart of sleep duration (in hours) across a series of buckets -- one day (Week/
// MonthView), one week's average (YearView), or one month's average (AllTimeView). Shared so
// all four call sites render identically and can't drift out of sync (see yearStats.ts's own
// "shared X so every place agrees" precedent).
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export interface SleepDurationPoint {
  x: string;
  hours: number | null;
}

export function SleepDurationChart({
  data,
  tickFormatter,
  tooltipLabelFormatter,
  interval,
}: {
  data: SleepDurationPoint[];
  tickFormatter: (x: string) => string;
  /** Defaults to `tickFormatter` -- pass a separate one when the tooltip should show more detail
   * than the (necessarily terser) axis tick, e.g. a full date vs. just a day-of-month number. */
  tooltipLabelFormatter?: (x: string) => string;
  /** Recharts' XAxis `interval` -- 0 shows every tick (fine for 7 days), a month/year/all-time
   * view with many more buckets should thin it out (see the same pattern already used by
   * WeekRunningStats' weekly-distance/VDOT charts). */
  interval?: number;
}) {
  // Hide entirely rather than an empty chart or a "no data" placeholder -- a summary view should
  // only show what it actually has. Callers gate their own heading on the same check (see
  // healthStats.ts::anyMetricHasData / this file's own `data.some(...)` shape) so this never
  // leaves a dangling title above nothing.
  if (!data.some((p) => p.hours != null)) {
    return null;
  }

  return (
    <ResponsiveContainer width="100%" height={140}>
      <BarChart data={data}>
        <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="x"
          tickFormatter={tickFormatter}
          stroke="var(--color-text-muted)"
          fontSize={11}
          interval={interval ?? 0}
        />
        {/* Zoomed to the data's own range (padded half an hour either side), not [0, max] --
            nightly sleep duration lives in a narrow band (roughly 6-8h), so a chart starting at
            0h makes every night look nearly the same height and hides the real differences.
            Recharts computes "dataMin - 0.5"/"dataMax + 0.5" in plain JS float arithmetic and
            renders whatever tick values fall inside that domain verbatim -- e.g. a data min of
            0.6 produces a domain floor of 0.09999999999999998 (0.6 - 0.5 in IEEE 754), which
            without an explicit tickFormatter renders as literally that raw digit string. The
            formatter rounds every tick for display regardless of what the internal domain math
            produces. */}
        <YAxis
          stroke="var(--color-text-muted)"
          fontSize={11}
          width={32}
          domain={["dataMin - 0.5", "dataMax + 0.5"]}
          tickFormatter={(v: number) => `${Math.round(v * 10) / 10}h`}
        />
        <Tooltip
          labelFormatter={(label) => (tooltipLabelFormatter ?? tickFormatter)(String(label))}
          formatter={(value) => [value == null ? "No data" : `${value} h`, "Sleep"]}
          contentStyle={{
            background: "var(--color-surface-raised)",
            border: "1px solid var(--color-border)",
          }}
        />
        <Bar dataKey="hours" fill="var(--color-cadence)" isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
