// Insights' "Training bands" chart: what share of total running time was spent at each pace,
// bucketed into fixed 30-second/km bands (this app's own pace convention, formatMinPerKm --
// not the min/mile numbers a reference pace-distribution widget might use). Each run is
// assigned to exactly one band by its own whole-activity average pace -- an honest
// simplification, not a fabricated finer-grained number than a run-level summary actually
// supports (a true within-run breakdown, e.g. warmup vs. a tempo interval, would need each
// run's own per-second stream fetched and summed -- far too expensive to do for the whole
// history client-side, and not what this chart claims to show).
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TooltipContentProps } from "recharts";

import type { ActivitySummary } from "../api/types";
import { effectiveDurationS, formatDurationHM, isPlausibleRunPace } from "../runningStats";
import "../styles/training-bands.css";

interface PaceBand {
  label: string;
  /** Inclusive lower bound, seconds/km (lower seconds = faster pace). */
  minSecPerKm: number;
  /** Exclusive upper bound, seconds/km; null = unbounded (the fastest band). */
  maxSecPerKm: number | null;
  color: string;
}

// Fast-to-slow, 30-second/km-wide bands. The slowest explicit band's upper edge (8:30/km, i.e.
// 510s) exactly matches IMPLAUSIBLE_RUN_PACE_MIN_PER_KM (runningStats.ts) -- anything at or past
// that pace already falls to isPlausibleRunPace's "Walk" bucket below, reusing the app's own
// real-data-calibrated threshold rather than inventing a second one. Colors are color-mix()
// blends across the app's own theme tokens (danger -> load -> elevation -> cadence), echoing a
// slow-to-fast red-to-teal gradient without introducing new arbitrary hex values.
const WALK_LABEL = "Walk";
const PACE_BANDS: PaceBand[] = [
  { label: "< 3:30", minSecPerKm: 0, maxSecPerKm: 210, color: "var(--color-cadence)" },
  {
    label: "3:30–4:00",
    minSecPerKm: 210,
    maxSecPerKm: 240,
    color: "color-mix(in srgb, var(--color-cadence) 65%, var(--color-elevation) 35%)",
  },
  { label: "4:00–4:30", minSecPerKm: 240, maxSecPerKm: 270, color: "var(--color-elevation)" },
  {
    label: "4:30–5:00",
    minSecPerKm: 270,
    maxSecPerKm: 300,
    color: "color-mix(in srgb, var(--color-elevation) 60%, var(--color-load) 40%)",
  },
  {
    label: "5:00–5:30",
    minSecPerKm: 300,
    maxSecPerKm: 330,
    color: "color-mix(in srgb, var(--color-elevation) 25%, var(--color-load) 75%)",
  },
  { label: "5:30–6:00", minSecPerKm: 330, maxSecPerKm: 360, color: "var(--color-load)" },
  {
    label: "6:00–6:30",
    minSecPerKm: 360,
    maxSecPerKm: 390,
    color: "color-mix(in srgb, var(--color-load) 60%, var(--color-danger) 40%)",
  },
  {
    label: "6:30–7:00",
    minSecPerKm: 390,
    maxSecPerKm: 420,
    color: "color-mix(in srgb, var(--color-load) 30%, var(--color-danger) 70%)",
  },
  {
    label: "7:00–7:30",
    minSecPerKm: 420,
    maxSecPerKm: 450,
    color: "color-mix(in srgb, var(--color-load) 10%, var(--color-danger) 90%)",
  },
  { label: "7:30–8:00", minSecPerKm: 450, maxSecPerKm: 480, color: "var(--color-danger)" },
  {
    label: "8:00–8:30",
    minSecPerKm: 480,
    maxSecPerKm: 510,
    color: "color-mix(in srgb, var(--color-danger) 80%, black 20%)",
  },
  {
    label: WALK_LABEL,
    minSecPerKm: 510,
    maxSecPerKm: null,
    color: "color-mix(in srgb, var(--color-danger) 55%, black 45%)",
  },
];

export interface TrainingBandRow {
  label: string;
  color: string;
  seconds: number;
  pct: number;
}

/** Buckets each run's whole-activity average pace into one `PACE_BANDS` entry, weighted by
 * moving time (falling back to elapsed time -- see effectiveDurationS). Activities with no
 * usable duration/distance are silently skipped, not counted as zero. */
export function computeTrainingBands(activities: ActivitySummary[]): TrainingBandRow[] {
  const secondsByLabel = new Map<string, number>(PACE_BANDS.map((b) => [b.label, 0]));
  let total = 0;

  for (const a of activities) {
    const durationS = effectiveDurationS(a);
    if (durationS == null || durationS <= 0 || a.distance_m == null || a.distance_m <= 0) continue;

    total += durationS;
    if (!isPlausibleRunPace(durationS, a.distance_m)) {
      secondsByLabel.set(WALK_LABEL, secondsByLabel.get(WALK_LABEL)! + durationS);
      continue;
    }
    const secPerKm = durationS / (a.distance_m / 1000);
    const band =
      PACE_BANDS.find((b) => secPerKm >= b.minSecPerKm && (b.maxSecPerKm == null || secPerKm < b.maxSecPerKm)) ??
      PACE_BANDS[PACE_BANDS.length - 2]!; // defensive fallback: slowest explicit (non-Walk) band
    secondsByLabel.set(band.label, secondsByLabel.get(band.label)! + durationS);
  }

  return PACE_BANDS.map((b) => {
    const seconds = secondsByLabel.get(b.label)!;
    return { label: b.label, color: b.color, seconds, pct: total > 0 ? (seconds / total) * 100 : 0 };
  });
}

interface BandTooltipPayload {
  payload: TrainingBandRow;
}

function isBandPayload(entry: unknown): entry is BandTooltipPayload {
  return (
    typeof entry === "object" &&
    entry != null &&
    "payload" in entry &&
    typeof (entry as { payload?: unknown }).payload === "object" &&
    (entry as { payload?: { label?: unknown } }).payload?.label != null
  );
}

function BandTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload) return null;
  const row = payload.find(isBandPayload)?.payload;
  if (!row) return null;
  return (
    <div className="training-bands__tooltip">
      <div>{row.label === WALK_LABEL ? "Walk pace" : `${row.label} /km`}</div>
      <div>
        {formatDurationHM(row.seconds)} · {Math.round(row.pct * 10) / 10}%
      </div>
    </div>
  );
}

export function TrainingBandsChart({ activities }: { activities: ActivitySummary[] }) {
  const [mode, setMode] = useState<"percentage" | "duration">("percentage");
  const rows = useMemo(() => computeTrainingBands(activities), [activities]);
  const hasData = rows.some((r) => r.seconds > 0);

  if (!hasData) return null;

  const dataKey = mode === "percentage" ? "pct" : "hours";
  const chartData = rows.map((r) => ({ ...r, hours: Math.round((r.seconds / 3600) * 100) / 100 }));

  return (
    <section className="card training-bands">
      <div className="training-bands__header">
        <h2>Training bands</h2>
        <div className="type-breakdown__toggle" role="group" aria-label="Show as">
          <button
            type="button"
            className={mode === "percentage" ? "is-active" : undefined}
            onClick={() => setMode("percentage")}
          >
            Percentage
          </button>
          <button
            type="button"
            className={mode === "duration" ? "is-active" : undefined}
            onClick={() => setMode("duration")}
          >
            Duration
          </button>
        </div>
      </div>
      <p className="chart-note">
        Share of total running time spent at each pace, from every run's own overall average
        pace -- slowest at right, fastest at left.
      </p>
      <ResponsiveContainer width="100%" height={320}>
        <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 48, left: 0 }}>
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
            dataKey={dataKey}
            type="number"
            domain={[0, (dataMax: number) => (mode === "percentage" ? Math.ceil(dataMax / 5) * 5 : Math.ceil(dataMax))]}
            stroke="var(--color-text-muted)"
            fontSize={11}
            width={40}
            unit={mode === "percentage" ? "%" : "h"}
          />
          <Tooltip content={BandTooltip} cursor={{ fill: "var(--color-surface-raised)" }} />
          <Bar dataKey={dataKey} isAnimationActive={false}>
            {chartData.map((row) => (
              <Cell key={row.label} fill={row.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
