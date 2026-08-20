// Insights' "Training bands" tab: what share of total running time was spent at each pace,
// summed from every running activity's own per-second (well, per-sample -- real device cadence)
// speed stream, computed server-side and precomputed at ingest time (see pace_bands.py) rather
// than approximated from each run's whole-activity average pace -- an earlier version of this
// component did the latter client-side, which hid all within-run pace variation (an interval
// session with fast reps and slow recovery jogging has the same average pace as a flat steady
// tempo run, but a completely different time-in-band profile).
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TooltipContentProps } from "recharts";

import { usePaceBands } from "../api/queries";
import type { PaceBandOut } from "../api/types";
import { formatDurationHM } from "../runningStats";
import "../styles/training-bands.css";

// Colors keyed by the exact label pace_bands.py's own PACE_BANDS produces (kept in sync by hand
// -- backend Python, no shared code with this TS frontend, same cross-language-duplication
// precedent as rules_pb.py/personalRecords). A label with no entry here (e.g. a future band
// pace_bands.py adds that this array hasn't been updated for yet) falls back to --color-load
// rather than rendering an uncoloured/invisible bar.
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

interface ChartRow extends PaceBandOut {
  color: string;
  pct: number;
  hours: number;
}

function toChartRows(bands: PaceBandOut[]): ChartRow[] {
  const total = bands.reduce((sum, b) => sum + b.seconds, 0);
  return bands.map((b) => ({
    ...b,
    color: BAND_COLORS[b.label] ?? FALLBACK_COLOR,
    pct: total > 0 ? (b.seconds / total) * 100 : 0,
    hours: Math.round((b.seconds / 3600) * 100) / 100,
  }));
}

interface BandTooltipPayload {
  payload: ChartRow;
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

export function TrainingBandsChart() {
  const [mode, setMode] = useState<"percentage" | "duration">("percentage");
  const bands = usePaceBands();
  const rows = useMemo(() => toChartRows(bands.data ?? []), [bands.data]);
  const hasData = rows.some((r) => r.seconds > 0);

  if (bands.isLoading) return <p>Loading…</p>;
  if (bands.isError) return <p role="alert">Could not load training bands.</p>;
  if (!hasData) return null;

  const dataKey = mode === "percentage" ? "pct" : "hours";

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
        Share of total running time spent at each pace, second by second across every run's own
        speed stream -- slowest at right, fastest at left.
      </p>
      <ResponsiveContainer width="100%" height={320}>
        <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 48, left: 0 }}>
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
            {rows.map((row) => (
              <Cell key={row.label} fill={row.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
