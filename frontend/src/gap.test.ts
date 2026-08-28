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
    const gap = gradeAdjustedPaceMinPerKm(6, -0.1);
    expect(gap).toBeGreaterThan(6);
  });

  it("clamps extreme uphill grades rather than extrapolating the polynomial unboundedly", () => {
    const clamped = gradeAdjustedPaceMinPerKm(6, 0.9);
    const atCap = gradeAdjustedPaceMinPerKm(6, 0.45);
    expect(clamped).toBeCloseTo(atCap, 5);
  });

  describe("the softened downhill curve (Strava's published post-2017 points)", () => {
    it("reads the -9% dip as the known 0.88 speed factor (pace / 0.88)", () => {
      expect(gradeAdjustedPaceMinPerKm(6, -0.09)).toBeCloseTo(6 / 0.88, 5);
    });

    it("reads -18% as fully recovered to no adjustment at all", () => {
      expect(gradeAdjustedPaceMinPerKm(6, -0.18)).toBeCloseTo(6, 5);
    });

    it("is much gentler than pure Minetti would be at -10% (Minetti demands ~1.67x, not ~1.14x)", () => {
      const gap = gradeAdjustedPaceMinPerKm(6, -0.1);
      const minettiWouldGive = 6 * (3.6 / 2.151706); // pure Minetti's own C(0)/C(-0.1) ratio
      expect(gap).toBeLessThan(minettiWouldGive);
    });

    it("stays flat at no adjustment for grades steeper than -18%, not runaway", () => {
      expect(gradeAdjustedPaceMinPerKm(6, -0.3)).toBeCloseTo(6, 5);
      expect(gradeAdjustedPaceMinPerKm(6, -0.9)).toBeCloseTo(6, 5);
    });

    it("has no jump discontinuity where the downhill branch meets the Minetti branch at 0%", () => {
      // Minetti's own curve has nonzero slope right at grade 0 (its linear coefficient, 19.5,
      // dominates near the origin), so neither side is flat -- the real correctness property is
      // that the two branches meet smoothly, not that either equals the unadjusted pace exactly.
      const justAbove = gradeAdjustedPaceMinPerKm(6, 1e-6);
      const justBelow = gradeAdjustedPaceMinPerKm(6, -1e-6);
      expect(justAbove).toBeCloseTo(justBelow, 3);
    });
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

// Per-lap GAP tests moved server-side -- see tests/test_gap.py::TestComputeLapGapSpeedsMps
// (gap.py::compute_lap_gap_speeds_mps), now the only implementation.
