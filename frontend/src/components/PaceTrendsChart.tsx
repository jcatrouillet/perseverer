// Insights' "Pace trends" chart: every VDOT-eligible run plotted over its own date, styled
// after the reference "SPI Pace Trends" widget the user pointed at -- a linear trend band
// through the whole history, the weekly-best-effort runs traced as a rising frontier in gold,
// races called out as red dots, everything else a plain grey dot. A duration bar chart sits
// underneath, always showing the full history; a click-move-click range selection on its bars
// (not a click-and-drag gesture -- see handleChartClick) filters the trend chart above to that
// period and recalculates its trend line.
import { useMemo, useState } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MouseHandlerDataParam, TooltipContentProps } from "recharts";
import { useLocation } from "wouter";

import type { ActivitySummary } from "../api/types";
import {
  formatClockDuration,
  formatDurationHM,
  vdotTrendPoints,
  type VdotTrendPoint,
} from "../runningStats";
import { predictRaceTimeS } from "../vdot";
import { ChartFullscreen } from "./ChartFullscreen";
import { ChartLegend } from "./ChartLegend";
import "../styles/pace-trends.css";

const FIVE_K_M = 5000;

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

/** Recharts' own `MouseHandlerDataParam.activeIndex` is typed `number | TooltipIndex |
 * undefined`, and `TooltipIndex` is `string | null` -- in practice it comes back as a numeric
 * *string* (confirmed via a live mouse event, not assumed), not a number, so a bare
 * `typeof === "number"` check silently rejects every real click/hover. */
function toDataIndex(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Ordinary least squares over {x: ts, y: vdot} -- `x` is normalized to days-since-first-point
 * before fitting so the regression isn't done on raw epoch-ms magnitudes (numerically noisy
 * for a fit this small). Null when there's nothing to draw a line through. */
function fitTrend(
  points: VdotTrendPoint[],
): { slope: number; intercept: number; residualStd: number } | null {
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
  // In-progress click-move-click range selection on the duration chart, tracked by index into
  // `points`/`durationData` (same order, same length) rather than by timestamp -- Recharts
  // hands back `activeIndex` directly on every mouse event, so there's no nearest-point-by-ts
  // lookup to get wrong. Not a click-and-drag gesture: the first click sets the anchor and
  // *stays* set while the mouse just moves (no button held down), previewing the range live;
  // the second click commits it.
  const [dragAnchorIndex, setDragAnchorIndex] = useState<number | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
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

  const isDragging = dragAnchorIndex != null && dragIndex != null;
  const handleChartClick = (state: MouseHandlerDataParam) => {
    const index = toDataIndex(state.activeIndex);
    if (index == null) return;
    if (dragAnchorIndex == null) {
      // First click: start the selection and preview it at zero width until the mouse moves.
      setDragAnchorIndex(index);
      setDragIndex(index);
      return;
    }
    // Second click: commit the previewed range (unless it's a click back on the same point,
    // which just cancels rather than selecting a single-point range).
    if (index !== dragAnchorIndex) {
      setSelection({
        startIndex: Math.min(dragAnchorIndex, index),
        endIndex: Math.max(dragAnchorIndex, index),
      });
    }
    setDragAnchorIndex(null);
    setDragIndex(null);
  };
  const handleChartMouseMove = (state: MouseHandlerDataParam) => {
    if (dragAnchorIndex == null) return;
    const index = toDataIndex(state.activeIndex);
    if (index == null) return;
    setDragIndex(index);
  };
  // What the duration chart currently highlights: the live drag in progress, or (once
  // released) the committed selection -- so the highlighted band never disappears until the
  // user explicitly resets it.
  const highlightStartIndex = isDragging
    ? Math.min(dragAnchorIndex, dragIndex)
    : isZoomed
      ? startIndex
      : null;
  const highlightEndIndex = isDragging
    ? Math.max(dragAnchorIndex, dragIndex)
    : isZoomed
      ? endIndex
      : null;

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
  const durationData = points.map((p) => ({
    ts: p.ts,
    hours: p.durationS != null ? Math.round((p.durationS / 3600) * 100) / 100 : null,
  }));
  // Ticked off `points`' own full-history range, not `visiblePoints`/`yearTicks` above -- this
  // chart always shows the full history regardless of the trend chart's own zoom (see the note
  // above it), so its axis ticks must stay independent of any zoom selection too.
  const durationSpanDays =
    points.length > 1 ? (points[points.length - 1]!.ts - points[0]!.ts) / MS_PER_DAY : 0;
  const useDurationYearTicks = durationSpanDays > 540;
  const durationYearTicks: number[] = [];
  if (useDurationYearTicks && points.length > 0) {
    for (
      let year = new Date(points[0]!.ts).getUTCFullYear();
      year <= new Date(points[points.length - 1]!.ts).getUTCFullYear();
      year++
    ) {
      durationYearTicks.push(Date.UTC(year, 0, 1));
    }
  }

  const goToActivity = (point: { payload?: { id?: string } }) => {
    if (point.payload?.id) setLocation(`/activities/${point.payload.id}`);
  };

  const weeklyTrendChange = trend != null ? Math.round(trend.slope * 7 * 100) / 100 : null;
  // "Cut N:NN from your estimated 5K finish time" -- the same VDOT/week rate above, translated
  // into a race-time-equivalent the way Daniels' own VDOT tables are conventionally read,
  // rather than left as a bare index number nobody has an intuition for. Computed from the
  // trend LINE's own start/end values (not the raw noisy data points), matching what "trend"
  // already means everywhere else in this component. Silently omitted (never a fabricated
  // guess) when either end's VDOT isn't representable as a real 5K time -- see
  // vdot.ts::predictRaceTimeS's own null cases.
  const fiveKTimeDeltaS = (() => {
    if (trend == null) return null;
    const spanDays = (lastTs - firstTs) / MS_PER_DAY;
    const vdotStart = trend.intercept;
    const vdotEnd = trend.intercept + trend.slope * spanDays;
    const timeStart = predictRaceTimeS(vdotStart, FIVE_K_M);
    const timeEnd = predictRaceTimeS(vdotEnd, FIVE_K_M);
    if (timeStart == null || timeEnd == null) return null;
    return timeStart - timeEnd; // positive = got faster (time cut), negative = got slower
  })();

  return (
    <section className="card pace-trends pace-trends--wide">
      {/* Wraps the whole section (both charts), not each separately: the duration chart below
          drives the trend chart's own zoom range via a click-move-click gesture (see the note
          right below) -- fullscreening just one would break that coupling. */}
      <ChartFullscreen as="h2" title="Pace trends">
        <p className="chart-note">
          Every run's VDOT over time -- gold traces the best run of each week, red dots are races,
          grey is everything else. Click once on the duration chart below, move the mouse, and click
          again to zoom into that period and recalculate its trend.
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
            {fiveKTimeDeltaS != null && Math.abs(fiveKTimeDeltaS) >= 1 && (
              <>
                {" "}
                · {fiveKTimeDeltaS >= 0 ? "Cut" : "Added"}{" "}
                {formatClockDuration(Math.abs(fiveKTimeDeltaS))}{" "}
                {fiveKTimeDeltaS >= 0 ? "from" : "to"} your estimated 5K finish time
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
              fill={RACE}
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
            { label: "Races", color: RACE },
            { label: "Other runs", color: GREY },
          ]}
        />

        <p className="chart-note">
          Duration of each run -- always the full history. Click once to start a period, move the
          mouse, click again to finish; the trend chart above zooms to match.
        </p>
        <ResponsiveContainer width="100%" height={204}>
          <BarChart
            data={durationData}
            margin={{ top: 0, right: 16, bottom: 0, left: 0 }}
            onClick={handleChartClick}
            onMouseMove={handleChartMouseMove}
            style={{ cursor: "crosshair", userSelect: "none" }}
          >
            <XAxis
              dataKey="ts"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              stroke="var(--color-text-muted)"
              fontSize={11}
              tickFormatter={useDurationYearTicks ? formatYearTick : formatMonthTick}
              ticks={useDurationYearTicks ? durationYearTicks : undefined}
            />
            <YAxis
              dataKey="hours"
              type="number"
              domain={[0, (dataMax: number) => Math.ceil(dataMax)]}
              stroke="var(--color-text-muted)"
              fontSize={11}
              width={32}
              unit="h"
            />
            <Tooltip
              formatter={(value) => [value == null ? "No data" : `${value} h`, "Duration"]}
              labelFormatter={() => ""}
              contentStyle={{
                background: "var(--color-surface-raised)",
                border: "1px solid var(--color-border)",
              }}
            />
            <Bar dataKey="hours" fill="var(--color-text-faint)" isAnimationActive={false} />
            {highlightStartIndex != null && highlightEndIndex != null && (
              <ReferenceArea
                x1={durationData[highlightStartIndex]!.ts}
                x2={durationData[highlightEndIndex]!.ts}
                fill="var(--color-accent)"
                fillOpacity={0.15}
                stroke="var(--color-accent)"
                strokeOpacity={0.4}
              />
            )}
          </BarChart>
        </ResponsiveContainer>
      </ChartFullscreen>
    </section>
  );
}
