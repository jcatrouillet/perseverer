import { memo, useMemo, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Scatter,
  XAxis,
  YAxis,
} from "recharts";
import { Link } from "wouter";

import type { ActivitySummary } from "../api/types";
import { prProgress, type PrPoint } from "../prProgress";
import { effectiveDurationS, formatClockDuration, formatMinPerKm } from "../runningStats";
import { displayActivityName } from "../yearStats";
import { ChartLegend } from "./ChartLegend";
import "../styles/pr-progress.css";

const RED = "var(--color-heart-rate)";
const BLUE = "var(--color-pace)";

function localToday() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function RunDot({
  cx,
  cy,
  payload,
  onSelect,
}: {
  cx?: number;
  cy?: number;
  payload?: PrPoint;
  onSelect: (id: string) => void;
}) {
  if (cx == null || cy == null || !payload?.activity) return <g />;
  const name = displayActivityName(payload.activity) ?? "Run";
  return (
    <a
      href={`/activities/${payload.activity.id}`}
      className="pr-progress__dot"
      tabIndex={0}
      aria-label={`${name}, ${payload.activity.local_date}, ${payload.distanceKm.toFixed(2)} km, VDOT ${payload.vdot.toFixed(1)}`}
      onMouseEnter={() => onSelect(payload.activity.id)}
      onFocus={() => onSelect(payload.activity.id)}
      onTouchStart={() => onSelect(payload.activity.id)}
    >
      <circle cx={cx} cy={cy} r={9} fill="transparent" />
      <circle
        cx={cx}
        cy={cy}
        r={4}
        fill={payload.older ? RED : BLUE}
        fillOpacity={0.7}
        stroke="var(--color-surface)"
        strokeWidth={1}
      />
    </a>
  );
}

export function PrProgressChart({
  activities,
  today = localToday(),
}: {
  activities: ActivitySummary[];
  today?: string;
}) {
  const data = useMemo(() => prProgress(activities, today), [activities, today]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = [...data.older, ...data.recent].find((p) => p.activity.id === selectedId);
  const duration = selected ? effectiveDurationS(selected.activity) : null;

  return (
    <section className="card pr-progress" aria-label="PR progress">
      <h2>PR progress</h2>
      <p className="chart-note">
        One best-VDOT run per week (Monday–Sunday), plotted at its actual distance. Red steps trace
        your older pace records; blue steps show recent improvements. Each step uses the fastest run
        covering at least that exact distance. Faster pace is higher.
      </p>
      <ChartLegend
        items={[
          { label: `One year or older · through ${data.cutoff}`, color: RED },
          { label: "Less than one year", color: BLUE },
        ]}
      />
      {!data.older.length && !data.recent.length ? (
        <p>No running activities with a VDOT, distance and duration are available yet.</p>
      ) : (
        <>
          <PrPlot data={data} onSelect={setSelectedId} />
          <div className="pr-progress__details" aria-live="polite">
            {selected ? (
              <>
                <strong>{displayActivityName(selected.activity) ?? "Run"}</strong>
                <span>
                  {selected.activity.local_date} · {selected.distanceKm.toFixed(2)} km ·{" "}
                  {formatMinPerKm(selected.pace / 60)} /km · VDOT {selected.vdot.toFixed(1)}
                  {duration != null ? ` · ${formatClockDuration(duration)}` : ""}
                  {selected.activity.is_race ? " · Race" : ""}
                </span>
                <Link href={`/activities/${selected.activity.id}`}>View run →</Link>
              </>
            ) : (
              <span>Hover or focus a dot for run details. Select a dot to open the run.</span>
            )}
          </div>
          {!data.older.length && (
            <p className="chart-note">
              No runs from a year ago or earlier are available for comparison yet.
            </p>
          )}
          {!!data.older.length && !data.recent.length && (
            <p className="chart-note">No weekly-best runs from the last year yet.</p>
          )}
          {!!data.older.length && !!data.recent.length && !data.improvements.length && (
            <p className="chart-note">
              No recent weekly best beats an older best at a comparable distance yet.
            </p>
          )}
        </>
      )}
    </section>
  );
}

const PrPlot = memo(function PrPlot({
  data,
  onSelect,
}: {
  data: ReturnType<typeof prProgress>;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="pr-progress__chart">
      <ResponsiveContainer width="100%" height={640}>
        <ComposedChart margin={{ top: 20, right: 24, bottom: 30, left: 12 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="distanceKm"
            name="Distance"
            unit=" km"
            domain={[0, (maximum: number) => Math.ceil(maximum)]}
            tickCount={9}
            tick={{ fill: "var(--color-text-muted)", fontSize: 12 }}
            label={{
              value: "Distance (km)",
              position: "bottom",
              fill: "var(--color-text-muted)",
            }}
          />
          <YAxis
            type="number"
            dataKey="pace"
            name="Pace"
            tickCount={9}
            domain={["auto", "auto"]}
            reversed
            tickFormatter={(value: number) => formatMinPerKm(value / 60)}
            tick={{ fill: "var(--color-text-muted)", fontSize: 12 }}
            label={{
              value: "Pace /km",
              angle: -90,
              position: "insideLeft",
              fill: "var(--color-text-muted)",
            }}
          />
          <Line
            data={
              data.baseline.length ? [{ ...data.baseline[0], distanceKm: 0 }, ...data.baseline] : []
            }
            type="stepBefore"
            dataKey="pace"
            stroke={RED}
            strokeWidth={3}
            dot={false}
            activeDot={false}
            isAnimationActive={false}
            legendType="none"
          />
          {data.improvements.map((segment, index) => (
            <Line
              key={index}
              data={segment}
              type="linear"
              dataKey="pace"
              stroke={BLUE}
              strokeWidth={3}
              dot={false}
              activeDot={false}
              isAnimationActive={false}
              legendType="none"
            />
          ))}
          <Scatter
            name="One year or older"
            data={data.older}
            fill={RED}
            shape={<RunDot onSelect={onSelect} />}
            isAnimationActive={false}
          />
          <Scatter
            name="Less than one year"
            data={data.recent}
            fill={BLUE}
            shape={<RunDot onSelect={onSelect} />}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
});
