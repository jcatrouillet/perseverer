// A ring of radial bars, one per 100m segment, showing how much each segment's pace deviated
// from the run's own average -- a steady pace draws a near-uniform ring, a run with real
// surges/fades draws a spiky one. The percentage in the centre is the same run's distance-
// weighted coefficient of variation (paceVariability.ts), not a value read off the drawing
// itself.
// Hand-rolled SVG rather than Recharts: this shape (bars radiating outward from a circle at
// arbitrary angles) has no clean Recharts primitive, the same reasoning that keeps the stream
// chart hand-rolled too (see AGENTS.md).
import type { PaceVariabilityResult } from "../paceVariability";
import { formatMinPerKm } from "../runningStats";
import "../styles/pace-variability.css";

const SIZE = 176;
const CENTER = SIZE / 2;
const INNER_RADIUS = 46;
const MAX_BAR_LENGTH = 38;
const MIN_BAR_LENGTH = 3;

export function PaceVariabilityChart({ result }: { result: PaceVariabilityResult }) {
  const { variabilityPct, segments } = result;
  const anglePerBar = 360 / segments.length;

  let cumulativeM = 0;

  return (
    <svg
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      className="pace-variability__svg"
      role="img"
      aria-label={`Pace variability: ${Math.round(variabilityPct)} percent`}
    >
      {segments.map((seg, i) => {
        // Starting at the top (-90deg) and going clockwise so the run's start sits at 12
        // o'clock, same reading direction as a clock face.
        const angle = ((i * anglePerBar - 90) * Math.PI) / 180;
        const length = MIN_BAR_LENGTH + seg.relativeDeviation * (MAX_BAR_LENGTH - MIN_BAR_LENGTH);
        const x1 = CENTER + INNER_RADIUS * Math.cos(angle);
        const y1 = CENTER + INNER_RADIUS * Math.sin(angle);
        const x2 = CENTER + (INNER_RADIUS + length) * Math.cos(angle);
        const y2 = CENTER + (INNER_RADIUS + length) * Math.sin(angle);
        const startKm = cumulativeM / 1000;
        cumulativeM += seg.distanceM;
        const endKm = cumulativeM / 1000;
        return (
          <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} className="pace-variability__bar">
            <title>
              {`${startKm.toFixed(2)}–${endKm.toFixed(2)} km: ${formatMinPerKm(seg.paceMinPerKm)} /km`}
            </title>
          </line>
        );
      })}
      <text
        x={CENTER}
        y={CENTER}
        className="pace-variability__value"
        textAnchor="middle"
        dominantBaseline="central"
      >
        {Math.round(variabilityPct)}%
      </text>
    </svg>
  );
}
