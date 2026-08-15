import { describe, expect, it } from "vitest";

import { gapSeriesMinPerKm, gradeAdjustedPaceMinPerKm } from "./gap";

describe("gradeAdjustedPaceMinPerKm", () => {
  it("returns the actual pace unchanged on flat ground", () => {
    expect(gradeAdjustedPaceMinPerKm(5, 0)).toBeCloseTo(5, 5);
  });

  it("makes uphill effort read as a faster (lower) pace", () => {
    // 10% uphill: cost(0.1) ~= 5.968 J/kg/m vs flat 3.6 -> gap = actual * 3.6/5.968 ~= 0.603x
    const gap = gradeAdjustedPaceMinPerKm(6, 0.1);
    expect(gap).toBeLessThan(6);
    expect(gap).toBeCloseTo(6 * (3.6 / 5.968214), 2);
  });

  it("makes downhill effort read as a slower (higher) pace", () => {
    // -10% downhill: cost(-0.1) ~= 2.152 J/kg/m vs flat 3.6 -> gap = actual * 3.6/2.152 ~= 1.673x
    const gap = gradeAdjustedPaceMinPerKm(6, -0.1);
    expect(gap).toBeGreaterThan(6);
    expect(gap).toBeCloseTo(6 * (3.6 / 2.151706), 2);
  });

  it("clamps extreme grades rather than extrapolating the polynomial unboundedly", () => {
    const clamped = gradeAdjustedPaceMinPerKm(6, 0.9);
    const atCap = gradeAdjustedPaceMinPerKm(6, 0.45);
    expect(clamped).toBeCloseTo(atCap, 5);
  });
});

describe("gapSeriesMinPerKm", () => {
  // 10m-spaced samples at a constant 5:00/km pace: flat for the first 250m, then a steady 5%
  // uphill grade for the remaining 250m.
  function buildStream() {
    const distanceM: number[] = [];
    const altitudeM: number[] = [];
    const paceMinPerKm: number[] = [];
    for (let i = 0; i <= 50; i++) {
      const d = i * 10;
      distanceM.push(d);
      altitudeM.push(d <= 250 ? 0 : (d - 250) * 0.05);
      paceMinPerKm.push(5);
    }
    return { distanceM, altitudeM, paceMinPerKm };
  }

  it("reads flat-ground points as GAP == actual pace", () => {
    const { distanceM, altitudeM, paceMinPerKm } = buildStream();
    const series = gapSeriesMinPerKm(distanceM, altitudeM, paceMinPerKm, 25);
    const atFlat = series[10]!; // d=100, well within the flat section
    expect(atFlat).toBeCloseTo(5, 5);
  });

  it("reads uphill points as a faster GAP than actual pace, matching the known 5% grade", () => {
    const { distanceM, altitudeM, paceMinPerKm } = buildStream();
    const series = gapSeriesMinPerKm(distanceM, altitudeM, paceMinPerKm, 25);
    const atUphill = series[40]!; // d=400, well within the uphill section
    expect(atUphill).toBeLessThan(5);
    expect(atUphill).toBeCloseTo(gradeAdjustedPaceMinPerKm(5, 0.05), 3);
  });

  it("is null wherever the input pace is null", () => {
    const { distanceM, altitudeM, paceMinPerKm } = buildStream();
    const pace = [...paceMinPerKm];
    pace[10] = null as unknown as number;
    const series = gapSeriesMinPerKm(distanceM, altitudeM, pace, 25);
    expect(series[10]).toBeNull();
  });

  it("is null wherever there is no altitude channel to derive a grade from", () => {
    const { distanceM, paceMinPerKm } = buildStream();
    const series = gapSeriesMinPerKm(distanceM, [], paceMinPerKm, 25);
    expect(series.every((v) => v == null)).toBe(true);
  });
});
