// The big popup graph itself (see GoalButton.tsx for why this never renders inline on the
// calendar page): a straight grey target-pace line across the whole period against the actual
// gold cumulative-distance line up to today, styled after the reference SPI/Strava goal widget
// the user pointed at. Hovering ANY point on the actual line -- not just today -- shows that
// day's own ahead/behind-goal difference (`GoalTooltip`'s `targetKm`/`diffKm`, precomputed once
// per point in `actualData` using the same `target_per_day_m * days_elapsed` formula
// `goals.py::compute_progress` already uses for `ahead_behind_m`, so the two never drift): index
// `i` in `progress.daily` already *is* `days_elapsed` (that array has exactly one entry per
// calendar day since the period started, gap-filled with 0 by the backend), so no date
// arithmetic needs re-deriving client-side. Only today's own point additionally gets the
// reference widget's richer "the N km you ran today puts you M km ahead/behind" sentence.
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

import type { GoalProgressOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import "../styles/goals.css";

const GOLD = "var(--color-load)";
const GREY = "var(--color-text-faint)";

function parseIsoUtc(iso: string): number {
  return new Date(`${iso}T00:00:00Z`).getTime();
}

function formatDateTick(ts: number): string {
  return new Date(ts).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export interface ChartPoint {
  ts: number;
  km: number;
  seriesLabel: "Actual" | "Target";
  // Only ever set on an "Actual" point -- see actualData's own construction for why the Target
  // line's own two endpoints don't need any of this.
  targetKm?: number;
  diffKm?: number;
  addedKm?: number;
  isToday?: boolean;
}

interface TooltipRow {
  payload: ChartPoint;
}

function isTooltipRow(entry: unknown): entry is TooltipRow {
  return (
    typeof entry === "object" &&
    entry != null &&
    "payload" in entry &&
    typeof (entry as { payload?: unknown }).payload === "object"
  );
}

// Exported for direct unit testing: driving this via real Recharts mouse-hover simulation would
// need a real ResizeObserver/layout engine jsdom doesn't provide, so tests render this plain
// function component directly with a fabricated payload instead.
export function GoalTooltip({
  active,
  payload,
  unitLabel,
}: TooltipContentProps & { unitLabel: string }) {
  if (!active || !payload || payload.length === 0) return null;
  // Both lines report their own nearest point at this cursor position, but only the Actual
  // point's payload carries the precomputed target/diff -- the Target row itself is redundant
  // once that's shown, so it's never rendered.
  const actual = payload.filter(isTooltipRow).find((r) => r.payload.seriesLabel === "Actual");
  if (!actual || actual.payload.targetKm == null || actual.payload.diffKm == null) return null;
  const { ts, km, targetKm, diffKm, addedKm, isToday } = actual.payload;
  const ahead = diffKm >= 0;
  const date = new Date(ts).toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
  return (
    <div className="goal-progress__tooltip">
      <div className="goal-progress__tooltip-date">{date}</div>
      {isToday && addedKm != null && (
        <p className="goal-progress__tooltip-today">
          The {addedKm.toFixed(1)} {unitLabel} you ran today puts you {Math.abs(diffKm).toFixed(1)}{" "}
          {unitLabel} {ahead ? "ahead of" : "behind"} your goal for today.
        </p>
      )}
      <div>
        Target: {targetKm.toFixed(1)} {unitLabel}
      </div>
      <div>
        Current: {km.toFixed(1)} {unitLabel}
      </div>
      <div className={ahead ? "goal-progress__tooltip-ahead" : "goal-progress__tooltip-behind"}>
        {ahead ? "Ahead" : "Behind"}: {ahead ? "+" : "-"}
        {Math.abs(diffKm).toFixed(1)} {unitLabel}
      </div>
    </div>
  );
}

/** The viewer's own local "today" (not UTC) -- matches how a visitor intuitively judges whether
 * the chart's own last point really is "today" versus a fully-completed past goal's last day. */
function todayLocalIso(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function GoalProgressChart({ progress }: { progress: GoalProgressOut }) {
  const { metersToDisplay, unitLabel } = useDistanceFormat();
  if (!progress.available || progress.goal == null || progress.period_end == null) return null;

  const { period_type, period_start } = progress.goal;
  // A week's period_start is already a full ISO date (the day its 7 days begin on).
  const periodStartTs = parseIsoUtc(
    period_type === "year"
      ? `${period_start}-01-01`
      : period_type === "month"
        ? `${period_start}-01`
        : period_start,
  );
  const periodEndTs = parseIsoUtc(progress.period_end);
  const targetKm = metersToDisplay(progress.goal.target_distance_m);
  const targetPerDayKm = metersToDisplay(progress.target_per_day_m ?? 0);
  const todayIso = todayLocalIso();

  const targetData: ChartPoint[] = [
    { ts: periodStartTs, km: 0, seriesLabel: "Target" },
    { ts: periodEndTs, km: targetKm, seriesLabel: "Target" },
  ];
  // progress.daily has exactly one entry per calendar day since the goal's period started (the
  // backend gap-fills days with no activity to 0 added distance) -- so index i is already
  // days_elapsed, the same quantity goals.py::compute_progress multiplies target_per_day_m by
  // for its own target_distance_as_of_today_m/ahead_behind_m. Reusing that identity here (rather
  // than re-deriving elapsed days from timestamps) guarantees this chart's own per-day target
  // never drifts from the summary tile above it (GoalButton.tsx), which reads those two fields
  // straight from the API for today's point specifically.
  const actualData: ChartPoint[] = progress.daily.map((p, i) => {
    const km = metersToDisplay(p.cumulative_distance_m);
    const daysElapsed = i + 1;
    const targetAtDay = targetPerDayKm * daysElapsed;
    const prevKm = i > 0 ? metersToDisplay(progress.daily[i - 1]!.cumulative_distance_m) : 0;
    return {
      ts: parseIsoUtc(p.local_date),
      km,
      seriesLabel: "Actual",
      targetKm: targetAtDay,
      diffKm: km - targetAtDay,
      addedKm: km - prevKm,
      isToday: i === progress.daily.length - 1 && p.local_date === todayIso,
    };
  });

  const maxKm = Math.max(targetKm, actualData[actualData.length - 1]?.km ?? 0);

  return (
    <div className="goal-progress">
      <ResponsiveContainer width="100%" height={680}>
        <ComposedChart margin={{ top: 8, right: 24, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={[periodStartTs, periodEndTs]}
            stroke="var(--color-text-muted)"
            fontSize={11}
            tickFormatter={formatDateTick}
          />
          <YAxis
            dataKey="km"
            type="number"
            domain={[0, Math.ceil(maxKm * 1.05)]}
            stroke="var(--color-text-muted)"
            fontSize={11}
            width={48}
            unit={` ${unitLabel}`}
          />
          <Tooltip content={(props) => <GoalTooltip {...props} unitLabel={unitLabel} />} />
          <Line
            data={targetData}
            dataKey="km"
            stroke={GREY}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            legendType="none"
          />
          <Line
            data={actualData}
            dataKey="km"
            stroke={GOLD}
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
            legendType="none"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
