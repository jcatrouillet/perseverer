import { describe, expect, it } from "vitest";

import { computePaceVariability } from "./paceVariability";
import type { KmSplit } from "./splits";

function split(km: number, distanceM: number, paceMinPerKm: number): KmSplit {
  return {
    km,
    distanceM,
    durationS: paceMinPerKm * 60 * (distanceM / 1000),
    paceMinPerKm,
    gapMinPerKm: null,
    elevChangeM: null,
    startIndex: 0,
    endIndex: 0,
  };
}

describe("computePaceVariability", () => {
  it("returns null for fewer than two splits", () => {
    expect(computePaceVariability([])).toBeNull();
    expect(computePaceVariability([split(1, 1000, 5)])).toBeNull();
  });

  it("is zero for a perfectly even pace across every split", () => {
    const splits = [split(1, 1000, 5), split(2, 1000, 5), split(3, 1000, 5)];
    const result = computePaceVariability(splits);
    expect(result).not.toBeNull();
    expect(result!.variabilityPct).toBeCloseTo(0, 5);
    expect(result!.segments.every((s) => s.relativeDeviation === 0)).toBe(true);
  });

  it("is positive when splits' paces differ, and the furthest-from-mean split reaches full relative deviation", () => {
    const splits = [split(1, 1000, 5), split(2, 1000, 6), split(3, 1000, 4.5)];
    const result = computePaceVariability(splits);
    expect(result).not.toBeNull();
    expect(result!.variabilityPct).toBeGreaterThan(0);
    // km 2 (6:00/km) is furthest from the mean -- its bar should be the tallest.
    const maxDeviationIndex = result!.segments.reduce(
      (best, s, i) => (s.relativeDeviation > result!.segments[best]!.relativeDeviation ? i : best),
      0,
    );
    expect(maxDeviationIndex).toBe(1);
    expect(result!.segments[1]!.relativeDeviation).toBeCloseTo(1, 5);
  });

  it("weights each split by its own distance -- a short surge swings the result less than a long one", () => {
    // Same absolute pace deviation (1 min/km faster than the 6:00/km baseline), but one scenario
    // has it over a full km and the other over just 100m.
    const longSurge = computePaceVariability([
      split(1, 1000, 6),
      split(2, 1000, 6),
      split(3, 1000, 5),
    ]);
    const shortSurge = computePaceVariability([
      split(1, 1000, 6),
      split(2, 1000, 6),
      split(3, 100, 5),
    ]);
    expect(longSurge).not.toBeNull();
    expect(shortSurge).not.toBeNull();
    expect(shortSurge!.variabilityPct).toBeLessThan(longSurge!.variabilityPct);
  });

  it("keeps one segment per split, in order", () => {
    const splits = [split(1, 1000, 5), split(2, 1000, 5.5), split(3, 500, 5.2)];
    const result = computePaceVariability(splits);
    expect(result!.segments).toHaveLength(3);
    expect(result!.segments.map((s) => s.paceMinPerKm)).toEqual([5, 5.5, 5.2]);
  });
});
