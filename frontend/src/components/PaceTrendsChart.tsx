// Insights' "Pace trends" chart: every VDOT-eligible run plotted over its own date, styled
// after the reference "SPI Pace Trends" widget the user pointed at -- a linear trend band
// through the whole history, the weekly-best-effort runs traced as a rising frontier in gold,
// races called out as open rings, everything else a plain grey dot. A synced duration bar
// chart sits underneath so a glance at any point on the trend also shows how long that day's
// run was.
import { useMemo, useState } from "react";
import {
  Area,
  Bar,
  BarChart,
  Brush,
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
import { formatDurationHM, vdotTrendPoints, type VdotTrendPoint } from "../runningStats";
import { ChartLegend } from "./ChartLegend";
import "../styles/pace-trends.css";

const GOLD = "var(--color-load)";
const GREY = "var(--color-text-faint)";
// Distinct from GOLD (weekly-best dots use gold too) so a race ring doesn't blend into a gold
// dot sitting right next to it -- races are frequently also a weekly best.
const RACE = "var(--color-heart-rate)";
const MS_PER_DAY = 86_400_000;

function formatYearTick(ts: number): string {
  return String(new Date(ts).getUTCFullYear());
}

function formatMonthTick(ts: number): string {
  return new Date(ts).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

/** Ordinary least squares over {x: ts, y: vdot} -- `x` is normalized to days-since-first-point
 * before fitting so the regression isn't done on raw epoch-ms magnitudes (numerically noisy
 * for a fit this small). Null when there's nothing to draw a line through. */
function fitTrend(points: VdotTrendPoint[]): { slope: number; intercept: number; residualStd: number } | null {
  if (points.length < 2) return null;
  const x0 = points[0]!.ts;
  const xs = points.map((p) => (p.ts - x0) / 86_400_000);
  const ys = points.map((p) => p.vdot);
  const n = xs.length;
  const meanX = xs.reduce((a, b) => a + b, 0) / n;
  const meanY = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i++) {
    num += (xs[i]! - meanX) * (ys[i]! - meanY);
    den += (xs[i]! - meanX) ** 2;
  }
  const slope = den === 0 ? 0 : num / den;
  const intercept = meanY - slope * meanX;
  const residuals = ys.map((y, i) => y - (slope * xs[i]! + intercept));
  const residualStd = Math.sqrt(residuals.reduce((s, r) => s + r * r, 0) / n);
  return { slope, intercept, residualStd };
}

interface RunTooltipPayload {
  payload: VdotTrendPoint;
}

function isRunPayload(entry: unknown): entry is RunTooltipPayload {
  return (
    typeof entry === "object" &&
    entry != null &&
    "payload" in entry &&
    typeof (entry as { payload?: unknown }).payload === "object" &&
    (entry as { payload?: { localDate?: unknown } }).payload?.localDate != null
  );
}

function VdotTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload) return null;
  const run = payload.find(isRunPayload)?.payload;
  if (!run) return null;
  return (
    <div className="pace-trends__tooltip">
      <div>{run.localDate}</div>
      <div>
        VDOT {run.vdot}
        {run.isRace ? " · Race" : ""}
      </div>
      {run.durationS != null && <div>{formatDurationHM(run.durationS)}</div>}
    </div>
  );
}

interface BrushRange {
  startIndex: number;
  endIndex: number;
}

export function PaceTrendsChart({ activities }: { activities: ActivitySummary[] }) {
  const [, setLocation] = useLocation();
  const [selection, setSelection] = useState<BrushRange | null>(null);
  const points = useMemo(() => vdotTrendPoints(activities), [activities]);

  if (points.length === 0) return null;

  // Clamped defensively -- `selection` holds indices from a possibly-earlier `points` array
  // (e.g. right after new activities load), so a stale range must never index past the end.
  const startIndex = selection ? Math.min(selection.startIndex, points.length - 1) : 0;
  const endIndex = selection
    ? Math.min(Math.max(selection.endIndex, startIndex), points.length - 1)
    : points.length - 1;
  const isZoomed = startIndex > 0 || endIndex < points.length - 1;
  const visiblePoints = isZoomed ? points.slice(startIndex, endIndex + 1) : points;

  const trend = fitTrend(visiblePoints);
  const firstTs = visiblePoints[0]!.ts;
  const lastTs = visiblePoints[visiblePoints.length - 1]!.ts;
  const spanDays = (lastTs - firstTs) / MS_PER_DAY;
  // Multi-year span: one tick per calendar year (Jan 1, UTC), not Recharts' own auto-placed
  // ticks on a continuous numeric axis -- those land at arbitrary points within a year and,
  // formatted through formatYearTick, show the same year two or three times in a row. Once
  // zoomed into a shorter window, year ticks would mostly collapse to one label, so switch to
  // month/year ticks and let Recharts place them automatically instead.
  const useYearTicks = spanDays > 540;
  const yearTicks: number[] = [];
  if (useYearTicks) {
    for (
      let year = new Date(firstTs).getUTCFullYear();
      year <= new Date(lastTs).getUTCFullYear();
      year++
    ) {
      yearTicks.push(Date.UTC(year, 0, 1));
    }
  }
  const bandData =
    trend != null
      ? [firstTs, lastTs].map((ts) => {
          const xDays = (ts - firstTs) / MS_PER_DAY;
          const center = trend.intercept + trend.slope * xDays;
          return {
            ts,
            trend: Math.round(center * 10) / 10,
            band: [
              Math.round((center - trend.residualStd) * 10) / 10,
              Math.round((center + trend.residualStd) * 10) / 10,
            ] as [number, number],
          };
        })
      : [];

  const bestPoints = visiblePoints.filter((p) => p.isWeeklyBest);
  const otherPoints = visiblePoints.filter((p) => !p.isWeeklyBest && !p.isRace);
  const racePoints = visiblePoints.filter((p) => p.isRace);
  const durationData = points.map((p) => ({ ts: p.ts, hours: p.durationS != null ? Math.round((p.durationS / 3600) * 100) / 100 : null }));

  const goToActivity = (point: { payload?: { id?: string } }) => {
    if (point.payload?.id) setLocation(`/activities/${point.payload.id}`);
  };

  const weeklyTrendChange = trend != null ? Math.round(trend.slope * 7 * 100) / 100 : null;

  return (
    <section className="card pace-trends pace-trends--wide">
      <h2>Pace trends</h2>
      <p className="chart-note">
        Every run's VDOT over time -- gold traces the best run of each week, red rings are
        races, grey is everything else. Drag on the timeline below to zoom into a period and
        recalculate its trend.
      </p>
      <div className="pace-trends__selection-bar">
        <span>
          <strong>{visiblePoints[0]!.localDate}</strong> to{" "}
          <strong>{visiblePoints[visiblePoints.length - 1]!.localDate}</strong>
          {weeklyTrendChange != null && (
            <>
              {" "}
              · trend {weeklyTrendChange >= 0 ? "+" : ""}
              {weeklyTrendChange} VDOT/week
            </>
          )}
        </span>
        {isZoomed && (
          <button type="button" className="pace-trends__reset" onClick={() => setSelection(null)}>
            Reset to full history
          </button>
        )}
      </div>
      <ResponsiveContainer width="100%" height={640}>
        <ComposedChart margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            stroke="var(--color-text-muted)"
            fontSize={11}
            tickFormatter={useYearTicks ? formatYearTick : formatMonthTick}
            ticks={useYearTicks ? yearTicks : undefined}
          />
          <YAxis
            dataKey="vdot"
            type="number"
            domain={["auto", "auto"]}
            stroke="var(--color-text-muted)"
            fontSize={11}
            width={32}
          />
          <Tooltip content={VdotTooltip} />
          {bandData.length > 0 && (
            <Area
              data={bandData}
              dataKey="band"
              stroke="none"
              fill={GREY}
              fillOpacity={0.16}
              isAnimationActive={false}
              legendType="none"
            />
          )}
          {bandData.length > 0 && (
            <Line
              data={bandData}
              dataKey="trend"
              stroke={GREY}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
              legendType="none"
            />
          )}
          {bestPoints.length > 1 && (
            <Line
              data={bestPoints}
              dataKey="vdot"
              stroke={GOLD}
              strokeWidth={1}
              strokeOpacity={0.6}
              dot={false}
              isAnimationActive={false}
              legendType="none"
            />
          )}
          <Scatter
            name="Other runs"
            data={otherPoints}
            fill={GREY}
            isAnimationActive={false}
            cursor="pointer"
            onClick={goToActivity}
          />
          <Scatter
            name="Best of week"
            data={bestPoints}
            fill={GOLD}
            isAnimationActive={false}
            cursor="pointer"
            onClick={goToActivity}
          />
          <Scatter
            name="Races"
            data={racePoints}
            fill="none"
            stroke={RACE}
            strokeWidth={2}
            shape="circle"
            isAnimationActive={false}
            cursor="pointer"
            onClick={goToActivity}
          />
        </ComposedChart>
      </ResponsiveContainer>

      <ChartLegend
        center
        items={[
          { label: "Best of week", color: GOLD },
          { label: "Races (ring)", color: RACE },
          { label: "Other runs", color: GREY },
        ]}
      />

      <p className="chart-note">
        Duration of each run -- always the full history. Drag the handles to select a period;
        the trend chart above zooms to match.
      </p>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={durationData} margin={{ top: 0, right: 16, bottom: 0, left: 0 }}>
          <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} hide />
          <YAxis dataKey="hours" type="number" stroke="var(--color-text-muted)" fontSize={11} width={32} unit="h" />
          <Tooltip
            formatter={(value) => [value == null ? "No data" : `${value} h`, "Duration"]}
            labelFormatter={() => ""}
            contentStyle={{
              background: "var(--color-surface-raised)",
              border: "1px solid var(--color-border)",
            }}
          />
          <Bar dataKey="hours" fill="var(--color-text-faint)" isAnimationActive={false} />
          <Brush
            dataKey="ts"
            height={32}
            travellerWidth={10}
            startIndex={startIndex}
            endIndex={endIndex}
            tickFormatter={formatYearTick}
            stroke="var(--color-accent)"
            fill="var(--color-surface-raised)"
            onChange={(range: { startIndex?: number; endIndex?: number }) =>
              setSelection({
                startIndex: range.startIndex ?? 0,
                endIndex: range.endIndex ?? points.length - 1,
              })
            }
          />
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
