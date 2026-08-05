// Hand-rolled SVG, extending StreamChart.tsx's exact pattern (bounded point count, already
// precomputed server-side, no zoom/pan/tooltip need -- see ADR 0008's lean-dependency call,
// still applicable here). Three series: CTL (Fitness) and ATL (Fatigue) share one axis; TSB
// (Form) gets its own axis since its range doesn't share scale with CTL/ATL. See
// docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
import { useMemo } from "react";

import type { FitnessDailyRollupOut } from "../api/types";

const WIDTH = 720;
const HEIGHT = 260;
const PADDING = 32;

export function FitnessChart({ series }: { series: FitnessDailyRollupOut[] }) {
  const chart = useMemo(() => {
    if (series.length < 2) return null;
    const dates = series.map((s) => new Date(s.local_date).getTime());
    const startMs = dates[0]!;
    const endMs = dates[dates.length - 1]!;
    const timeRange = endMs - startMs || 1;
    const xFor = (i: number) =>
      PADDING + ((dates[i]! - startMs) / timeRange) * (WIDTH - 2 * PADDING);

    const loadMax = Math.max(1, ...series.map((s) => s.ctl), ...series.map((s) => s.atl));
    const yLoad = (v: number) => HEIGHT - PADDING - (v / loadMax) * (HEIGHT - 2 * PADDING);

    const tsbValues = series.map((s) => s.tsb);
    const tsbMin = Math.min(0, ...tsbValues);
    const tsbMax = Math.max(0, ...tsbValues);
    const tsbRange = tsbMax - tsbMin || 1;
    const yTsb = (v: number) =>
      HEIGHT - PADDING - ((v - tsbMin) / tsbRange) * (HEIGHT - 2 * PADDING);

    const pathFor = (accessor: (s: FitnessDailyRollupOut) => number, y: (v: number) => number) =>
      series.map((s, i) => `${i === 0 ? "M" : "L"} ${xFor(i)} ${y(accessor(s))}`).join(" ");

    return {
      ctlPath: pathFor((s) => s.ctl, yLoad),
      atlPath: pathFor((s) => s.atl, yLoad),
      tsbPath: pathFor((s) => s.tsb, yTsb),
      zeroLineY: yTsb(0),
    };
  }, [series]);

  if (!chart) {
    return <p>Not enough data to chart Fitness &amp; Form yet.</p>;
  }

  const latest = series[series.length - 1]!;

  return (
    <div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        width="100%"
        role="img"
        aria-label="Fitness (CTL), Fatigue (ATL), and Form (TSB) over time"
      >
        <line
          x1={0}
          y1={chart.zeroLineY}
          x2={WIDTH}
          y2={chart.zeroLineY}
          stroke="currentColor"
          strokeDasharray="4 4"
          opacity={0.3}
        />
        <path d={chart.ctlPath} fill="none" stroke="currentColor" strokeWidth={2} />
        <path
          d={chart.atlPath}
          fill="none"
          stroke="currentColor"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
        <path d={chart.tsbPath} fill="none" stroke="currentColor" strokeWidth={2} opacity={0.5} />
      </svg>
      <p>
        Latest ({latest.local_date}): Fitness (CTL) {latest.ctl.toFixed(1)} · Fatigue (ATL){" "}
        {latest.atl.toFixed(1)} · Form (TSB) {latest.tsb.toFixed(1)}
      </p>
      <p>
        Solid line: Fitness (CTL). Thin dashed line: Fatigue (ATL). Faint line: Form (TSB) against
        its own zero-line axis (dashed).
      </p>
    </div>
  );
}
