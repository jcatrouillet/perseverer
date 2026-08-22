// Distance-weighted coefficient of variation of pace across a run's own segments -- how
// consistent the pace was, as a percentage of the run's own (distance-weighted) average pace.
// Weighted by each segment's own distance, not a flat per-segment average, so a short final
// partial segment doesn't swing the result out of proportion to how much of the run it actually
// covers. Takes whatever segments the caller computed (splits.ts::computeSplitsAtInterval) --
// ActivityRoute.tsx passes 100m segments here, finer than the whole-km splits the Splits table
// itself shows, so the variability ring reflects real pace texture within each kilometre rather
// than smoothing it away.
import type { KmSplit } from "./splits";

export interface PaceVariabilitySegment {
  /** This segment's own pace, min/km. */
  paceMinPerKm: number;
  /** Distance this segment actually covers -- equal for every segment except a shorter final
   * one, matching computeSplitsAtInterval's own trailing-partial-segment behaviour. */
  distanceM: number;
  /** 0..1 -- this split's |pace - mean| relative to the run's single largest deviation, so the
   * chart's most extreme split always reaches full bar length regardless of how large the run's
   * overall variability is. */
  relativeDeviation: number;
}

export interface PaceVariabilityResult {
  variabilityPct: number;
  segments: PaceVariabilitySegment[];
}

/** Null when there's nothing meaningful to compare -- a single split (or none) has no
 * variability to speak of. */
export function computePaceVariability(splits: KmSplit[]): PaceVariabilityResult | null {
  if (splits.length < 2) return null;

  const totalDistance = splits.reduce((sum, s) => sum + s.distanceM, 0);
  if (totalDistance <= 0) return null;

  const weightedMean =
    splits.reduce((sum, s) => sum + s.paceMinPerKm * s.distanceM, 0) / totalDistance;
  if (weightedMean <= 0) return null;

  const weightedVariance =
    splits.reduce((sum, s) => sum + s.distanceM * (s.paceMinPerKm - weightedMean) ** 2, 0) /
    totalDistance;
  const stdev = Math.sqrt(weightedVariance);
  const variabilityPct = (stdev / weightedMean) * 100;

  const deviations = splits.map((s) => Math.abs(s.paceMinPerKm - weightedMean));
  const maxDeviation = Math.max(...deviations, 1e-9);
  const segments: PaceVariabilitySegment[] = splits.map((s, i) => ({
    paceMinPerKm: s.paceMinPerKm,
    distanceM: s.distanceM,
    relativeDeviation: deviations[i]! / maxDeviation,
  }));

  return { variabilityPct, segments };
}
