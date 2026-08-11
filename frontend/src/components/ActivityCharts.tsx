// Multi-panel per-second activity charts (Milestone C of docs/adr/0010-phase-6.1-frontend-
// design.md's plan) -- replaces StreamChart.tsx's single-channel-with-a-selector approach with
// one small chart per channel the activity actually has, all sharing one Recharts `syncId` so
// hovering any panel moves a synced cursor/tooltip across all of them at once. Lap boundaries
// draw as vertical reference lines using each lap's own start time, converted to the same
// elapsed-seconds x-axis the stream panels use.
//
// Only a channel the activity actually recorded gets a panel -- there is no fixed six-panel
// layout with empty slots for a treadmill run with no GPS speed, or a run with no power meter.
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { LapOut, StreamResponse } from "../api/types";
import type { IconName } from "./Icon";
import { toneColor, type Tone } from "../metricStyle";
import { formatClockDuration, isPaceSport, streamSpeedValue } from "../runningStats";
import { Icon } from "./Icon";

interface Panel {
  key: string;
  title: string;
  icon: IconName;
  tone: Tone;
  kind: "area" | "line";
  unit: string;
  transform: (raw: number | null) => number | null;
  formatValue: (v: number) => string;
}

function panelsFor(sport: string): Panel[] {
  const paceSport = isPaceSport(sport);
  return [
    {
      key: "altitude_m",
      title: "Elevation",
      icon: "mountain",
      tone: "elevation",
      kind: "area",
      unit: "m",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} m`,
    },
    {
      key: "speed_mps",
      title: paceSport ? "Pace" : "Speed",
      icon: "gauge",
      tone: "pace",
      kind: "line",
      unit: paceSport ? "/km" : "km/h",
      transform: (v) => streamSpeedValue(sport, v),
      formatValue: (v) => (paceSport ? `${formatPace(v)} /km` : `${v.toFixed(1)} km/h`),
    },
    {
      key: "heart_rate",
      title: "Heart rate",
      icon: "heart",
      tone: "hr",
      kind: "line",
      unit: "bpm",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} bpm`,
    },
    {
      key: "respiration_rate",
      title: "Respiration",
      icon: "pulse",
      tone: "cadence",
      kind: "line",
      unit: "brpm",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} brpm`,
    },
    {
      key: "cadence",
      title: "Cadence",
      icon: "steps",
      tone: "cadence",
      kind: "line",
      unit: "spm",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} spm`,
    },
    {
      key: "power",
      title: "Power",
      icon: "bolt",
      tone: "power",
      kind: "line",
      unit: "W",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} W`,
    },
    {
      key: "temperature",
      title: "Temperature",
      icon: "thermometer",
      tone: "load",
      kind: "line",
      unit: "°C",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)}°C`,
    },
  ];
}

function formatPace(minPerKm: number): string {
  const min = Math.floor(minPerKm);
  const sec = Math.round((minPerKm - min) * 60);
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

export function ActivityCharts({
  stream,
  laps,
  sport,
}: {
  stream: StreamResponse;
  laps: LapOut[];
  sport: string;
}) {
  if (stream.timestamps.length === 0) {
    return <p>No stream data available.</p>;
  }

  const startMs = new Date(stream.timestamps[0]!).getTime();
  const elapsed = stream.timestamps.map((t) => (new Date(t).getTime() - startMs) / 1000);
  const maxT = elapsed[elapsed.length - 1] ?? 0;

  // Lap starts after the activity's own start, converted to the same elapsed-seconds axis the
  // stream panels use -- the first lap's own start essentially coincides with t=0, so it's
  // skipped as a redundant line sitting on top of the y-axis.
  const lapMarks = laps
    .map((lap) => (new Date(lap.start_time_utc).getTime() - startMs) / 1000)
    .filter((t) => t > 1 && t < maxT);

  const panels = panelsFor(sport).filter((panel) => {
    const raw = stream.series[panel.key];
    return raw != null && raw.some((v) => panel.transform(v) != null);
  });

  if (panels.length === 0) {
    return <p>No stream data available.</p>;
  }

  return (
    <div className="activity-charts">
      {panels.map((panel) => {
        const data = elapsed.map((t, i) => ({
          t,
          v: panel.transform(stream.series[panel.key]?.[i] ?? null),
        }));
        const color = toneColor(panel.tone);
        return (
          <div className="activity-charts__panel" key={panel.key}>
            <h4>
              <span className={`icon-chip tone-${panel.tone}`}>
                <Icon name={panel.icon} />
              </span>
              {panel.title}
            </h4>
            <ResponsiveContainer width="100%" height={160}>
              {panel.kind === "area" ? (
                <AreaChart data={data} syncId="activity-charts" margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id={`activity-chart-fill-${panel.key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={color} stopOpacity={0.32} />
                      <stop offset="100%" stopColor={color} stopOpacity={0.03} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="t"
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    tickFormatter={formatClockDuration}
                  />
                  <YAxis stroke="var(--color-text-muted)" fontSize={11} width={40} domain={["auto", "auto"]} />
                  {lapMarks.map((t) => (
                    <ReferenceLine key={t} x={t} stroke="var(--color-text-faint)" strokeDasharray="2 2" />
                  ))}
                  <Tooltip
                    labelFormatter={(t) => formatClockDuration(Number(t))}
                    formatter={(value) => [panel.formatValue(Number(value)), panel.title] as [string, string]}
                    contentStyle={{
                      background: "var(--color-surface-raised)",
                      border: "1px solid var(--color-border)",
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="v"
                    stroke={color}
                    fill={`url(#activity-chart-fill-${panel.key})`}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                </AreaChart>
              ) : (
                <LineChart data={data} syncId="activity-charts" margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="t"
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    tickFormatter={formatClockDuration}
                  />
                  <YAxis stroke="var(--color-text-muted)" fontSize={11} width={40} domain={["auto", "auto"]} />
                  {lapMarks.map((t) => (
                    <ReferenceLine key={t} x={t} stroke="var(--color-text-faint)" strokeDasharray="2 2" />
                  ))}
                  <Tooltip
                    labelFormatter={(t) => formatClockDuration(Number(t))}
                    formatter={(value) => [panel.formatValue(Number(value)), panel.title] as [string, string]}
                    contentStyle={{
                      background: "var(--color-surface-raised)",
                      border: "1px solid var(--color-border)",
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="v"
                    stroke={color}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                </LineChart>
              )}
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}
