// Insights' "Speed trends" tab for rides (SpeedTrendsChart.tsx): each ride's average moving speed
// over its own date, with a straight trend line through the period shown. The cycling
// counterpart of Pace trends, which plots VDOT -- a running model with no meaning for a ride.
import type { ActivitySummary } from "./api/types";
import { effectiveDurationS } from "./runningStats";

export interface SpeedTrendPoint {
  id: string;
  name: string | null;
  localDate: string;
  /** UTC midnight of `localDate`, for a time axis. */
  ts: number;
  speedKmh: number;
  distanceM: number;
}

// Below this, a "ride" is a mis-tagged walk or a short shuffle; above it, a GPS glitch or a
// car trip left in the recording. Neither belongs on a speed trend.
const MIN_RIDE_DISTANCE_M = 2000;
const MIN_PLAUSIBLE_KMH = 5;
const MAX_PLAUSIBLE_KMH = 60;

export function speedTrendPoints(activities: ActivitySummary[]): SpeedTrendPoint[] {
  const points: SpeedTrendPoint[] = [];
  for (const a of activities) {
    const seconds = effectiveDurationS(a);
    if (a.local_date == null || a.distance_m == null || seconds == null || seconds <= 0) continue;
    if (a.distance_m < MIN_RIDE_DISTANCE_M) continue;
    const speedKmh = (a.distance_m / seconds) * 3.6;
    if (speedKmh < MIN_PLAUSIBLE_KMH || speedKmh > MAX_PLAUSIBLE_KMH) continue;
    points.push({
      id: a.id,
      name: a.name,
      localDate: a.local_date,
      ts: Date.parse(`${a.local_date}T00:00:00Z`),
      speedKmh,
      distanceM: a.distance_m,
    });
  }
  return points.sort((x, y) => x.ts - y.ts);
}

/** Least-squares line through (ts, speed), as its two end points; null under two points or when
 * every point shares one date (no slope to fit). */
export function speedTrendLine(
  points: SpeedTrendPoint[],
): { start: { ts: number; speedKmh: number }; end: { ts: number; speedKmh: number } } | null {
  if (points.length < 2) return null;
  const n = points.length;
  const meanX = points.reduce((s, p) => s + p.ts, 0) / n;
  const meanY = points.reduce((s, p) => s + p.speedKmh, 0) / n;
  let sxx = 0;
  let sxy = 0;
  for (const p of points) {
    sxx += (p.ts - meanX) ** 2;
    sxy += (p.ts - meanX) * (p.speedKmh - meanY);
  }
  if (sxx === 0) return null;
  const slope = sxy / sxx;
  const at = (ts: number) => meanY + slope * (ts - meanX);
  const first = points[0]!.ts;
  const last = points[n - 1]!.ts;
  return { start: { ts: first, speedKmh: at(first) }, end: { ts: last, speedKmh: at(last) } };
}
