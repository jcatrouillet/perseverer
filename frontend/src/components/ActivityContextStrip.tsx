// Milestone E of docs/adr/0010-phase-6.1-frontend-design.md's plan: a compact bubble/sparkline
// strip reading GET /activities/{id}/context. A deliberately smaller, honest analog of Strava/
// intervals.icu's richer comparison views -- not a reproduction of their proprietary models,
// just the athlete's own real recent efforts (bubble size = distance) plus a plain percentile
// sentence, computed straightforwardly from the same archive rather than any vendor's black box.
import {
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import type { ActivityContextOut, UnitPreference } from "../api/types";
import { ChartFullscreen } from "./ChartFullscreen";
import {
  kmhToDisplaySpeed,
  metersToDisplayDistance,
  paceMinPerDisplayUnit,
  speedUnitLabel,
  useDistanceFormat,
} from "../formatDistance";
import { toneColor } from "../metricStyle";
import { formatMinPerKm, isPaceSport } from "../runningStats";

function avgPaceOrSpeedValue(
  sport: string,
  durationS: number,
  distanceM: number,
  unit: UnitPreference,
): number {
  return isPaceSport(sport)
    ? paceMinPerDisplayUnit(durationS / (distanceM / 1000), unit)
    : kmhToDisplaySpeed(distanceM / 1000 / (durationS / 3600), unit);
}

interface ContextPoint {
  id: string;
  date: string;
  distanceKm: number;
  value: number;
  isCurrent: boolean;
}

/** A custom tooltip, not Recharts' default `formatter` -- a scatter point is plotted from three
 * separate dataKeys (X's date, Y's pace/speed, Z's bubble-size distance), and Recharts' default
 * tooltip runs *every one* of them through the same `formatter`, producing three "Pace" lines
 * (one of them the date string coerced through pace formatting into "NaN:NaN /km") instead of
 * one. Reading the point's own original data off `payload[0].payload` sidesteps that entirely. */
function ContextTooltip({
  active,
  payload,
  paceSport,
  unit,
}: {
  active?: boolean;
  payload?: { payload: ContextPoint }[];
  paceSport: boolean;
  unit: UnitPreference;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0]!.payload;
  const unitLabel = unit === "imperial" ? "mi" : "km";
  return (
    <div className="activity-context__tooltip">
      <div className="activity-context__tooltip-date">{p.date}</div>
      <div>
        {paceSport ? `${formatMinPerKm(p.value)} /${unitLabel}` : `${p.value.toFixed(1)} ${speedUnitLabel(unit)}`}
      </div>
      <div className="activity-context__tooltip-distance">{p.distanceKm.toFixed(2)} {unitLabel}</div>
    </div>
  );
}

export function ActivityContextStrip({
  context,
  sport,
  currentActivityId,
}: {
  context: ActivityContextOut;
  sport: string;
  currentActivityId: string;
}) {
  const paceSport = isPaceSport(sport);
  const { unit } = useDistanceFormat();
  const points = context.recent
    .filter((r) => r.distance_m > 0 && r.duration_s > 0)
    .map((r) => ({
      id: r.id,
      date: r.local_date ?? "",
      distanceKm: metersToDisplayDistance(r.distance_m, unit),
      value: avgPaceOrSpeedValue(sport, r.duration_s, r.distance_m, unit),
      isCurrent: r.id === currentActivityId,
    }));

  if (points.length < 2 && context.percentile_rank == null) {
    return null;
  }

  const color = toneColor("pace");

  const headline = context.percentile_rank != null && (
    <p className="activity-context__headline">
      Faster than <strong>{context.percentile_rank.toFixed(0)}%</strong> of{" "}
      {context.comparable_count} similar {sport.replace(/_/g, " ")} effort
      {context.comparable_count === 1 ? "" : "s"} within 15% distance.
    </p>
  );

  if (points.length < 2) {
    return (
      <section className="card activity-context">
        <h2>Recent efforts</h2>
        {headline}
      </section>
    );
  }

  return (
    <section className="card activity-context">
      <ChartFullscreen as="h2" title="Recent efforts">
        {headline}
        <ResponsiveContainer width="100%" height={140}>
          <ScatterChart margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              type="category"
              stroke="var(--color-text-muted)"
              fontSize={11}
              tickFormatter={(d: string) => d.slice(5)}
            />
            <YAxis
              dataKey="value"
              stroke="var(--color-text-muted)"
              fontSize={11}
              width={44}
              reversed={paceSport}
              domain={["dataMin", "dataMax"]}
              tickFormatter={(v: number) => (paceSport ? formatMinPerKm(v) : v.toFixed(0))}
            />
            {/* Bubble size = distance -- the "bubble" half of the plan's "bubble/sparkline
                strip", so a long run and a short shakeout at the same pace read differently. */}
            <ZAxis dataKey="distanceKm" range={[36, 260]} />
            <Tooltip content={<ContextTooltip paceSport={paceSport} unit={unit} />} />
            <Scatter data={points} isAnimationActive={false}>
              {points.map((p) => (
                <Cell
                  key={p.id}
                  fill={color}
                  fillOpacity={p.isCurrent ? 1 : 0.35}
                  stroke={p.isCurrent ? color : "none"}
                  strokeWidth={p.isCurrent ? 2 : 0}
                />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </ChartFullscreen>
    </section>
  );
}
