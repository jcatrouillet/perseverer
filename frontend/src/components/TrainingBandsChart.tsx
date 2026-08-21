// Insights' "Training bands" tab: three views of the same underlying data (every running
// activity's own per-second, well, per-sample -- real device cadence -- speed stream, computed
// server-side and precomputed at ingest time, see pace_bands.py), laid out to match Pace trends'
// own full-bleed width and top-chart/bottom-duration-chart shape. The top chart is one 100%-
// stacked bar per run, showing what *share of that one run* was spent at each pace -- an interval
// session with fast reps and slow recovery jogging reads very differently here than a flat steady
// tempo run, which is the whole point (an earlier version of this component only had the middle
// chart, built from each run's whole-activity *average* pace, which hid exactly that variation).
// The middle chart sums the same data athlete-wide, in absolute time per band. The bottom chart
// is each run's own duration, styled identically to PaceTrendsChart's own bottom chart.
import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  useXAxisScale,
  useYAxisScale,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";
import { useLocation } from "wouter";

import { usePaceBands, usePaceBandsByActivity } from "../api/queries";
import type { ActivityPaceBandsOut, PaceBandOut } from "../api/types";
import { parseIsoDate } from "../dateUtils";
import { formatDurationHM } from "../runningStats";
import "../styles/training-bands.css";

// Colors keyed by the exact label pace_bands.py's own PACE_BANDS produces, fast to slow (kept in
// sync by hand -- backend Python, no shared code with this TS frontend, same cross-language-
// duplication precedent as rules_pb.py/personalRecords). A label with no entry here (e.g. a
// future band pace_bands.py adds that this array hasn't been updated for yet) falls back to
// --color-load rather than rendering an uncoloured/invisible bar. Also doubles as the canonical
// band order for both charts, via Object.keys -- insertion order is guaranteed for string keys.
const WALK_LABEL = "Walk";
const BAND_COLORS: Record<string, string> = {
  "< 3:30": "var(--color-cadence)",
  "3:30-4:00": "color-mix(in srgb, var(--color-cadence) 65%, var(--color-elevation) 35%)",
  "4:00-4:30": "var(--color-elevation)",
  "4:30-5:00": "color-mix(in srgb, var(--color-elevation) 60%, var(--color-load) 40%)",
  "5:00-5:30": "color-mix(in srgb, var(--color-elevation) 25%, var(--color-load) 75%)",
  "5:30-6:00": "var(--color-load)",
  "6:00-6:30": "color-mix(in srgb, var(--color-load) 60%, var(--color-danger) 40%)",
  "6:30-7:00": "color-mix(in srgb, var(--color-load) 30%, var(--color-danger) 70%)",
  "7:00-7:30": "color-mix(in srgb, var(--color-load) 10%, var(--color-danger) 90%)",
  "7:30-8:00": "var(--color-danger)",
  "8:00-8:30": "color-mix(in srgb, var(--color-danger) 80%, black 20%)",
  [WALK_LABEL]: "color-mix(in srgb, var(--color-danger) 55%, black 45%)",
};
const FALLBACK_COLOR = "var(--color-load)";
const BAND_LABELS_FAST_TO_SLOW = Object.keys(BAND_COLORS);
const MS_PER_DAY = 86_400_000;

function formatYearTick(ts: number): string {
  return String(new Date(ts).getUTCFullYear());
}

function formatMonthTick(ts: number): string {
  return new Date(ts).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

// ---------------------------------------------------------------------------
// Bottom chart: athlete-wide total time per band.
// ---------------------------------------------------------------------------

interface AggregateRow extends PaceBandOut {
  color: string;
  hours: number;
}

function toAggregateRows(bands: PaceBandOut[]): AggregateRow[] {
  return bands.map((b) => ({
    ...b,
    color: BAND_COLORS[b.label] ?? FALLBACK_COLOR,
    hours: Math.round((b.seconds / 3600) * 100) / 100,
  }));
}

interface AggregateTooltipPayload {
  payload: AggregateRow;
}

function isAggregatePayload(entry: unknown): entry is AggregateTooltipPayload {
  return (
    typeof entry === "object" &&
    entry != null &&
    "payload" in entry &&
    typeof (entry as { payload?: unknown }).payload === "object" &&
    (entry as { payload?: { label?: unknown } }).payload?.label != null
  );
}

function AggregateTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload) return null;
  const row = payload.find(isAggregatePayload)?.payload;
  if (!row) return null;
  return (
    <div className="training-bands__tooltip">
      <div>{row.label === WALK_LABEL ? "Walk pace" : `${row.label} /km`}</div>
      <div>{formatDurationHM(row.seconds)}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top chart: one 100%-stacked bar per run.
// ---------------------------------------------------------------------------

interface CompositionRow {
  activityId: string;
  ts: number;
  localDate: string | null;
  pctByLabel: Record<string, number>;
  hours: number;
}

function toCompositionRows(activities: ActivityPaceBandsOut[]): CompositionRow[] {
  const rows = activities
    .filter((a) => a.local_date != null)
    .map((a) => {
      const total = a.bands.reduce((sum, b) => sum + b.seconds, 0);
      const pctByLabel: Record<string, number> = {};
      for (const b of a.bands) {
        pctByLabel[b.label] = total > 0 ? (b.seconds / total) * 100 : 0;
      }
      return {
        activityId: a.activity_id,
        ts: parseIsoDate(a.local_date!).getTime(),
        localDate: a.local_date,
        pctByLabel,
        // The same total this activity's own bands already sum to -- not activity.moving_duration_s
        // (a separate fetch this component doesn't otherwise need), so the duration chart's totals
        // stay exactly consistent with the composition chart directly above it.
        hours: Math.round((total / 3600) * 100) / 100,
      };
    });
  return rows.sort((a, b) => a.ts - b.ts);
}

// Recharts derives a numeric-axis Bar's width from the single *smallest* pixel gap between any
// two data points (see node_modules/recharts es6/util/ChartUtils.js -- getBandSizeOfAxis), and
// -- confirmed by reading combineAllBarPositions.js -- an explicit `barSize` prop can only shrink
// a stacked bar *below* that computed band, never widen it past it. With ~1000 runs spanning a
// decade, one same-day (or near-same-day) pair collapses that auto-computed band to a hairline
// for the *entire* chart, even in stretches where runs are days apart and would otherwise read as
// a continuous block -- there is no supported way to fix this by configuring <Bar/> itself.
// CompositionBars below renders past that limitation entirely: real <Bar/> elements stay mounted
// (invisible) purely so Recharts' own stacking math, Tooltip hover-tracking, and click handling
// keep working exactly as already wired up, while this component reads the resolved axis scales
// directly (Recharts 3's useXAxisScale/useYAxisScale -- "render arbitrary elements anywhere") and
// draws its own, deliberately wider, rects underneath them. Width is sized off the *typical*
// (median) gap between runs rather than the single tightest one, so it closes up in well-
// populated stretches of the timeline while leaving genuinely sparse stretches (this athlete's
// own 2016-2019) still visibly separate -- matching how a real "training load over time" view
// should read. PLOT_WIDTH_PX approximates training-bands--wide's own rendered plot width (this
// component has no resize observer to measure it live) minus the chart's left/right margins.
const PLOT_WIDTH_PX = 1650;
const WIDEN_FACTOR = 1.5;

function estimateBarWidthPx(rows: CompositionRow[]): number {
  if (rows.length < 2) return 2;
  const gaps: number[] = [];
  for (let i = 1; i < rows.length; i++) {
    gaps.push(rows[i]!.ts - rows[i - 1]!.ts);
  }
  gaps.sort((a, b) => a - b);
  const medianGapMs = gaps[Math.floor(gaps.length / 2)]!;
  const totalSpanMs = rows[rows.length - 1]!.ts - rows[0]!.ts;
  if (totalSpanMs <= 0) return 2;
  const pxPerMs = PLOT_WIDTH_PX / totalSpanMs;
  return Math.min(12, Math.max(2, medianGapMs * pxPerMs * WIDEN_FACTOR));
}

// Bottom (0%) to top (100%) of the stack, same slow-to-fast order the real stacked <Bar/>
// elements below render in.
const BAND_LABELS_SLOW_TO_FAST = [...BAND_LABELS_FAST_TO_SLOW].reverse();

function CompositionBars({ rows, barWidthPx }: { rows: CompositionRow[]; barWidthPx: number }) {
  const xScale = useXAxisScale();
  const yScale = useYAxisScale();
  if (!xScale || !yScale) return null;

  return (
    <g style={{ pointerEvents: "none" }}>
      {rows.map((row) => {
        const cx = xScale(row.ts);
        if (cx == null) return null;
        let cumulative = 0;
        return (
          <g key={row.activityId}>
            {BAND_LABELS_SLOW_TO_FAST.map((label) => {
              const pct = row.pctByLabel[label] ?? 0;
              if (pct <= 0) return null;
              const y1 = yScale(cumulative);
              const y2 = yScale(cumulative + pct);
              cumulative += pct;
              if (y1 == null || y2 == null) return null;
              return (
                <rect
                  key={label}
                  x={cx - barWidthPx / 2}
                  y={Math.min(y1, y2)}
                  width={barWidthPx}
                  height={Math.abs(y1 - y2)}
                  fill={BAND_COLORS[label] ?? FALLBACK_COLOR}
                />
              );
            })}
          </g>
        );
      })}
    </g>
  );
}

interface CompositionTooltipPayload {
  payload: CompositionRow;
}

function isCompositionPayload(entry: unknown): entry is CompositionTooltipPayload {
  return (
    typeof entry === "object" &&
    entry != null &&
    "payload" in entry &&
    typeof (entry as { payload?: unknown }).payload === "object" &&
    (entry as { payload?: { localDate?: unknown } }).payload?.localDate != null
  );
}

function CompositionTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload) return null;
  const row = payload.find(isCompositionPayload)?.payload;
  if (!row) return null;
  const topBands = (Object.entries(row.pctByLabel) as [string, number][])
    .filter(([, pct]) => pct > 0)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 3);
  return (
    <div className="training-bands__tooltip">
      <div>{row.localDate}</div>
      {topBands.map(([label, pct]) => (
        <div key={label}>
          {label === WALK_LABEL ? "Walk" : `${label} /km`} · {Math.round(pct)}%
        </div>
      ))}
    </div>
  );
}

export function TrainingBandsChart() {
  const [, setLocation] = useLocation();
  const aggregate = usePaceBands();
  const byActivity = usePaceBandsByActivity();

  const aggregateRows = useMemo(() => toAggregateRows(aggregate.data ?? []), [aggregate.data]);
  const compositionRows = useMemo(
    () => toCompositionRows(byActivity.data ?? []),
    [byActivity.data],
  );
  const barWidthPx = useMemo(() => estimateBarWidthPx(compositionRows), [compositionRows]);

  const hasAggregateData = aggregateRows.some((r) => r.seconds > 0);
  const hasCompositionData = compositionRows.length > 0;

  if (aggregate.isLoading || byActivity.isLoading) return <p>Loading…</p>;
  if (aggregate.isError || byActivity.isError) {
    return <p role="alert">Could not load training bands.</p>;
  }
  if (!hasAggregateData && !hasCompositionData) return null;

  const firstTs = compositionRows[0]?.ts;
  const lastTs = compositionRows[compositionRows.length - 1]?.ts;
  const spanDays = firstTs != null && lastTs != null ? (lastTs - firstTs) / MS_PER_DAY : 0;
  const useYearTicks = spanDays > 540;
  const yearTicks: number[] = [];
  if (useYearTicks && firstTs != null && lastTs != null) {
    for (let year = new Date(firstTs).getUTCFullYear(); year <= new Date(lastTs).getUTCFullYear(); year++) {
      yearTicks.push(Date.UTC(year, 0, 1));
    }
  }

  const goToActivity = (bar: { payload?: CompositionRow }) => {
    const activityId = bar.payload?.activityId;
    if (activityId) setLocation(`/activities/${activityId}`);
  };

  return (
    <section className="card training-bands training-bands--wide">
      <h2>Training bands</h2>

      {hasCompositionData && (
        <>
          <p className="chart-note">
            Every run as its own bar -- the share of that one run spent at each pace, second by
            second across its own speed stream. Slow paces sit at the bottom, fast at the top.
          </p>
          <ResponsiveContainer width="100%" height={360}>
            <BarChart data={compositionRows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
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
                type="number"
                domain={[0, 100]}
                allowDataOverflow
                tickFormatter={(v: number) => String(Math.round(v))}
                stroke="var(--color-text-muted)"
                fontSize={11}
                width={40}
                unit="%"
              />
              <Tooltip content={CompositionTooltip} cursor={{ fill: "var(--color-surface-raised)" }} />
              <CompositionBars rows={compositionRows} barWidthPx={barWidthPx} />
              {BAND_LABELS_SLOW_TO_FAST.map((label) => (
                <Bar
                  key={label}
                  dataKey={(row: CompositionRow) => row.pctByLabel[label] ?? 0}
                  stackId="composition"
                  // Invisible -- the real, wider bars are CompositionBars above. This one stays
                  // mounted purely so Recharts' own stacking math, Tooltip hover-tracking, and
                  // click handling (goToActivity) keep working: fillOpacity 0 hides the paint
                  // without affecting hit-testing, which SVG bases on the fill being *set*, not
                  // its opacity.
                  fill={BAND_COLORS[label] ?? FALLBACK_COLOR}
                  fillOpacity={0}
                  isAnimationActive={false}
                  cursor="pointer"
                  onClick={goToActivity}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </>
      )}

      {hasAggregateData && (
        <>
          <p className="chart-note">
            Total time spent at each pace, summed across the whole running history.
          </p>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={aggregateRows} margin={{ top: 8, right: 16, bottom: 48, left: 0 }}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="label"
                stroke="var(--color-text-muted)"
                fontSize={11}
                interval={0}
                angle={-40}
                textAnchor="end"
                height={64}
              />
              <YAxis
                dataKey="hours"
                type="number"
                domain={[0, (dataMax: number) => Math.ceil(dataMax)]}
                stroke="var(--color-text-muted)"
                fontSize={11}
                width={40}
                unit="h"
              />
              <Tooltip content={AggregateTooltip} cursor={{ fill: "var(--color-surface-raised)" }} />
              <Bar dataKey="hours" isAnimationActive={false}>
                {aggregateRows.map((row) => (
                  <Cell key={row.label} fill={row.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </>
      )}

      {hasCompositionData && (
        <>
          <p className="chart-note">Duration of each run -- always the full history.</p>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={compositionRows} margin={{ top: 0, right: 16, bottom: 0, left: 0 }}>
              <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} hide />
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
              <Bar
                dataKey="hours"
                fill="var(--color-text-faint)"
                isAnimationActive={false}
                cursor="pointer"
                onClick={goToActivity}
              />
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </section>
  );
}
