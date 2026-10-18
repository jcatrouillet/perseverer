// Insights' cycling "Power trends" tab: each ride's best 20-minute power over its date, with the
// trailing-90-day FTP as a line, in W or W/kg (powerTrends.ts). Speed stays one click away as a
// fallback for rides without a power meter, and is what the tab opens on when no ride has power.
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";
import { useLocation } from "wouter";

import { useCyclingPower } from "../api/queries";
import type { ActivitySummary } from "../api/types";
import { toneColor } from "../metricStyle";
import { ftpLine, powerTrendPoints, type PowerTrendPoint, type PowerUnit } from "../powerTrends";
import { ChartLegend } from "./ChartLegend";
import { LoadingSpinner } from "./LoadingSpinner";
import { SpeedTrendsChart } from "./SpeedTrendsChart";
import "../styles/pace-trends.css";

type View = PowerUnit | "speed";

const POWER = toneColor("power");
const RIDE = toneColor("ride");

function formatYearTick(ts: number): string {
  return String(new Date(ts).getUTCFullYear());
}

export function PowerTrendsChart({
  activities,
  excludeCommutes = false,
}: {
  activities: ActivitySummary[];
  excludeCommutes?: boolean;
}) {
  const [, navigate] = useLocation();
  const power = useCyclingPower(excludeCommutes);
  const history = useMemo(() => power.data?.history ?? [], [power.data]);
  const points = useMemo(() => powerTrendPoints(activities, history), [activities, history]);
  const line = useMemo(() => ftpLine(history), [history]);
  const hasWeight = points.some((p) => p.wattsPerKg != null);
  const [chosen, setChosen] = useState<View | null>(null);
  // Opens on W/kg (W without a weight), or on speed when no ride has 20 minutes of power.
  const view: View = chosen ?? (points.length === 0 ? "speed" : hasWeight ? "wkg" : "w");

  if (power.isLoading) return <LoadingSpinner />;

  const toggle = (
    <div className="type-breakdown__toggle" role="group" aria-label="Show">
      {(
        [
          ["w", "W"],
          ["wkg", "W/kg"],
          ["speed", "Speed"],
        ] as const
      ).map(([key, label]) => (
        <button
          key={key}
          type="button"
          className={view === key ? "is-active" : undefined}
          aria-pressed={view === key}
          disabled={key !== "speed" && points.length === 0}
          onClick={() => setChosen(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );

  if (view === "speed") {
    return (
      <>
        <div className="power-trends__controls">{toggle}</div>
        {points.length === 0 && (
          <p className="chart-note">
            No ride has 20 minutes of power yet (it needs a power meter or a smart trainer), so this
            shows speed, which wind, hills, drafting and virtual rides all move.
          </p>
        )}
        <SpeedTrendsChart activities={activities} />
      </>
    );
  }

  const perKg = view === "wkg";
  const unit = perKg ? "W/kg" : "W";
  const dots = points
    .map((p) => ({ ...p, value: perKg ? p.wattsPerKg : p.watts }))
    .filter((p) => p.value != null);
  const lineData = line
    .map((p) => ({ ts: p.ts, ftp: perKg ? p.ftpWPerKg : p.ftpW }))
    .filter((p) => p.ftp != null);
  const format = (v: number) => (perKg ? v.toFixed(2) : String(Math.round(v)));

  function PowerTooltip({ active, payload }: TooltipContentProps) {
    if (!active || !payload) return null;
    const p = payload.find((entry) => entry.payload?.watts != null)?.payload as
      (PowerTrendPoint & { value: number }) | undefined;
    if (!p) return null;
    return (
      <div className="pace-trends__tooltip">
        <div>
          <strong>{p.name ?? "Ride"}</strong>
        </div>
        <div>{p.localDate}</div>
        <div>
          Best 20 min: {Math.round(p.watts)} W
          {p.wattsPerKg != null && ` · ${p.wattsPerKg.toFixed(2)} W/kg`}
        </div>
      </div>
    );
  }

  return (
    <section className="card">
      <div className="power-trends__header">
        <h2>Power trends</h2>
        {toggle}
      </div>
      <p className="chart-note">
        Each dot is one ride&apos;s best 20 minutes; the line is your FTP over the trailing 90 days
        (95% of the best 20 minutes in that window). Only the {dots.length} rides recorded with a
        power meter or smart trainer and at least 20 minutes of power appear. Click a dot to open
        that ride.
      </p>
      <ResponsiveContainer width="100%" height={360}>
        <ComposedChart margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={formatYearTick}
            stroke="var(--color-text-muted)"
            fontSize={12}
          />
          <YAxis
            type="number"
            domain={["auto", "auto"]}
            tickFormatter={format}
            unit={` ${unit}`}
            width={72}
            stroke="var(--color-text-muted)"
            fontSize={12}
          />
          <Tooltip content={PowerTooltip} />
          <Scatter
            data={dots}
            dataKey="value"
            fill={POWER}
            fillOpacity={0.8}
            cursor="pointer"
            isAnimationActive={false}
            onClick={(entry: { payload?: PowerTrendPoint }) => {
              if (entry.payload) navigate(`/activities/${entry.payload.id}`);
            }}
          />
          {lineData.length > 0 && (
            <Line
              data={lineData}
              dataKey="ftp"
              stroke={RIDE}
              strokeWidth={2}
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <ChartLegend
        center
        items={[
          { label: `Best 20 min (${unit})`, color: POWER },
          { label: `FTP, 90 days (${unit})`, color: RIDE },
        ]}
      />
    </section>
  );
}
