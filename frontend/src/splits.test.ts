import { describe, expect, it } from "vitest";

import { computeKmSplits } from "./splits";

// Synthetic 2.5km stream at a constant 5:00/km pace, 100m/30s samples: flat for km 1, a steady
// 5% uphill grade for km 2, flat again for the final 500m partial split.
function buildStream() {
  const distanceM: number[] = [];
  const elapsedS: number[] = [];
  const altitudeM: number[] = [];
  for (let i = 0; i <= 25; i++) {
    const d = i * 100;
    distanceM.push(d);
    elapsedS.push(d * 0.3); // 300s per km => 5:00/km
    if (d <= 1000) altitudeM.push(0);
    else if (d < 2000) altitudeM.push((d - 1000) * 0.05);
    else altitudeM.push(50);
  }
  return { distanceM, elapsedS, altitudeM };
}

describe("computeKmSplits", () => {
  it("splits a 2.5km stream into two full km splits plus a 500m partial", () => {
    const { distanceM, elapsedS, altitudeM } = buildStream();
    const splits = computeKmSplits(distanceM, elapsedS, altitudeM);

    expect(splits).toHaveLength(3);
    expect(splits.map((s) => s.km)).toEqual([1, 2, 3]);
    expect(splits.map((s) => s.distanceM)).toEqual([1000, 1000, 500]);
  });

  it("computes real pace and duration for each split, flat first km at 5:00/km", () => {
    const { distanceM, elapsedS, altitudeM } = buildStream();
    const [first] = computeKmSplits(distanceM, elapsedS, altitudeM);

    expect(first!.durationS).toBeCloseTo(300, 5);
    expect(first!.paceMinPerKm).toBeCloseTo(5, 5);
    expect(first!.elevChangeM).toBeCloseTo(0, 5);
    expect(first!.gapMinPerKm).toBeCloseTo(5, 2); // flat -> GAP ~= actual pace
  });

  it("shows a real net elevation gain on the uphill split, with GAP reading faster than actual", () => {
    const { distanceM, elapsedS, altitudeM } = buildStream();
    const [, second] = computeKmSplits(distanceM, elapsedS, altitudeM);

    expect(second!.elevChangeM).toBeCloseTo(50, 5);
    expect(second!.paceMinPerKm).toBeCloseTo(5, 5);
    expect(second!.gapMinPerKm).toBeLessThan(second!.paceMinPerKm);
  });

  it("returns startIndex/endIndex bracketing each split's stream range", () => {
    const { distanceM, elapsedS, altitudeM } = buildStream();
    const splits = computeKmSplits(distanceM, elapsedS, altitudeM);

    expect(splits[0]!.startIndex).toBe(0);
    expect(distanceM[splits[0]!.endIndex]).toBe(1000);
    expect(distanceM[splits[1]!.endIndex]).toBe(2000);
  });

  it("has null gapMinPerKm/elevChangeM when there is no altitude channel", () => {
    const { distanceM, elapsedS } = buildStream();
    const splits = computeKmSplits(distanceM, elapsedS, undefined);

    expect(splits.every((s) => s.gapMinPerKm == null && s.elevChangeM == null)).toBe(true);
    expect(splits).toHaveLength(3);
  });

  it("drops a trailing remainder under 50m rather than emitting a near-zero split", () => {
    const distanceM = [0, 500, 1000, 1020];
    const elapsedS = [0, 150, 300, 306];
    const splits = computeKmSplits(distanceM, elapsedS);

    expect(splits).toHaveLength(1);
    expect(splits[0]!.distanceM).toBe(1000);
  });

  it("returns an empty list for a stream with no distance channel", () => {
    expect(computeKmSplits([], [])).toEqual([]);
  });
});
