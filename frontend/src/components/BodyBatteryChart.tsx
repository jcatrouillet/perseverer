// Body battery over the course of one day -- real per-reading data from GET /health/stream
// (garmin.daily_body_battery.level), not the sparse 8-checkpoint summary this project
// deliberately avoided charting (see health/json_parser.py::parse_daily_body_battery_json's own
// docstring for why the real per-minute-ish series was worth fetching live instead). Same
// small-line-chart shape as SleepDurationChart.tsx, just a Line instead of a Bar.
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useTimeFormat } from "../formatTime";
import { toneColor } from "../metricStyle";

export interface BodyBatteryPoint {
  timestamp: string;
  level: number;
}

export function BodyBatteryChart({ points }: { points: BodyBatteryPoint[] }) {
  const { formatTimeOfDay } = useTimeFormat();
  if (points.length === 0) return null;

  const formatTick = (timestamp: string) => formatTimeOfDay(new Date(timestamp));

  return (
    <ResponsiveContainer width="100%" height={140}>
      <LineChart data={points}>
        <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="timestamp"
          tickFormatter={formatTick}
          stroke="var(--color-text-muted)"
          fontSize={11}
        />
        {/* Body battery is always a 0-100 scale (Garmin's own convention) -- fixed, not zoomed
            to the data's own range, so a day that never dips low doesn't visually read as more
            volatile than it was. */}
        <YAxis
          domain={[0, 100]}
          stroke="var(--color-text-muted)"
          fontSize={11}
          width={28}
          ticks={[0, 25, 50, 75, 100]}
        />
        <Tooltip
          labelFormatter={(label) => formatTick(String(label))}
          formatter={(value) => [`${value}`, "Body Battery"]}
          contentStyle={{
            background: "var(--color-surface-raised)",
            border: "1px solid var(--color-border)",
          }}
        />
        <Line
          type="monotone"
          dataKey="level"
          stroke={toneColor("cadence")}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
