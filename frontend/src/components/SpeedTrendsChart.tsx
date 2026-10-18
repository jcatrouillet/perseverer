// Insights' "Speed trends" tab, shown when the sport switch is on Cycling: every ride's average
// moving speed over its own date (speedTrends.ts), in the ride tone, with a straight trend line.
// Clicking a dot opens that ride.
import { useMemo } from "react";
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

import type { ActivitySummary } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { toneColor } from "../metricStyle";
import { speedTrendLine, speedTrendPoints, type SpeedTrendPoint } from "../speedTrends";
import "../styles/pace-trends.css";

const RIDE = toneColor("ride");

function formatYearTick(ts: number): string {
  return String(new Date(ts).getUTCFullYear());
}

export function SpeedTrendsChart({ activities }: { activities: ActivitySummary[] }) {
  const [, navigate] = useLocation();
  const { kmhToDisplay, speedUnitLabel, formatDistance } = useDistanceFormat();
  const points = useMemo(() => speedTrendPoints(activities), [activities]);
  const trend = useMemo(() => speedTrendLine(points), [points]);

  if (points.length === 0) {
    return (
      <section className="card">
        <h2>Speed trends</h2>
        <p className="chart-note">No rides with a distance and a time yet.</p>
      </section>
    );
  }

  const data = points.map((p) => ({ ...p, speed: kmhToDisplay(p.speedKmh) }));
  const trendData = trend
    ? [
        { ts: trend.start.ts, trend: kmhToDisplay(trend.start.speedKmh) },
        { ts: trend.end.ts, trend: kmhToDisplay(trend.end.speedKmh) },
      ]
    : [];
  const change = trend ? kmhToDisplay(trend.end.speedKmh - trend.start.speedKmh) : null;

  function SpeedTooltip({ active, payload }: TooltipContentProps) {
    if (!active || !payload) return null;
    const p = payload.find((entry) => entry.payload?.speed != null)?.payload as
      (SpeedTrendPoint & { speed: number }) | undefined;
    if (!p) return null;
    return (
      <div className="pace-trends__tooltip">
        <div>
          <strong>{p.name ?? "Ride"}</strong>
        </div>
        <div>{p.localDate}</div>
        <div>
          {p.speed.toFixed(1)} {speedUnitLabel} · {formatDistance(p.distanceM)}
        </div>
      </div>
    );
  }

  return (
    <section className="card">
      <h2>Speed trends</h2>
      <p className="chart-note">
        Each ride's average moving speed, with a straight trend line through all {points.length}{" "}
        rides
        {change != null &&
          ` (${change >= 0 ? "+" : ""}${change.toFixed(1)} ${speedUnitLabel} from first to last)`}
        . Climbs, wind and indoor or virtual rides all move a ride's speed, so read the trend over
        many rides, not one. Click a dot to open that ride.
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
            dataKey="speed"
            type="number"
            domain={["auto", "auto"]}
            unit={` ${speedUnitLabel}`}
            width={72}
            stroke="var(--color-text-muted)"
            fontSize={12}
          />
          <Tooltip content={SpeedTooltip} />
          <Scatter
            data={data}
            dataKey="speed"
            fill={RIDE}
            fillOpacity={0.75}
            cursor="pointer"
            onClick={(entry: { payload?: SpeedTrendPoint }) => {
              if (entry.payload) navigate(`/activities/${entry.payload.id}`);
            }}
          />
          {trendData.length > 0 && (
            <Line
              data={trendData}
              dataKey="trend"
              stroke={RIDE}
              strokeWidth={2}
              strokeDasharray="6 4"
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </section>
  );
}
