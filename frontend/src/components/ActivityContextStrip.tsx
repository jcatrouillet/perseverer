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

import type { ActivityContextOut } from "../api/types";
import { toneColor } from "../metricStyle";
import { formatMinPerKm, isPaceSport } from "../runningStats";

function avgPaceOrSpeedValue(sport: string, durationS: number, distanceM: number): number {
  return isPaceSport(sport) ? durationS / 60 / (distanceM / 1000) : distanceM / 1000 / (durationS / 3600);
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
  const points = context.recent
    .filter((r) => r.distance_m > 0 && r.duration_s > 0)
    .map((r) => ({
      id: r.id,
      date: r.local_date ?? "",
      distanceKm: r.distance_m / 1000,
      value: avgPaceOrSpeedValue(sport, r.duration_s, r.distance_m),
      isCurrent: r.id === currentActivityId,
    }));

  if (points.length < 2 && context.percentile_rank == null) {
    return null;
  }

  const color = toneColor("pace");

  return (
    <div className="activity-context">
      {context.percentile_rank != null && (
        <p className="activity-context__headline">
          Faster than <strong>{context.percentile_rank.toFixed(0)}%</strong> of{" "}
          {context.comparable_count} similar {sport.replace(/_/g, " ")} effort
          {context.comparable_count === 1 ? "" : "s"} within 15% distance.
        </p>
      )}
      {points.length >= 2 && (
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
            <Tooltip
              formatter={(value) =>
                paceSport
                  ? [`${formatMinPerKm(Number(value))} /km`, "Pace"]
                  : [`${Number(value).toFixed(1)} km/h`, "Speed"]
              }
              contentStyle={{
                background: "var(--color-surface-raised)",
                border: "1px solid var(--color-border)",
              }}
            />
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
      )}
    </div>
  );
}
