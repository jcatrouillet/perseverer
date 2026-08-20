import { describe, expect, it } from "vitest";

import { predictRaceTimeS, vdotForEffort } from "./vdot";

describe("vdotForEffort", () => {
  it("matches the backend's own compute_vdot for a known 5000m/1500s effort", () => {
    // Cross-checked directly against `uv run python -c "from sporthealth.vdot import
    // compute_vdot; print(compute_vdot(5000, 1500))"` -- 38.30936290766015.
    expect(vdotForEffort(5000, 1500)).toBeCloseTo(38.30936290766015, 9);
  });

  it("matches the backend for a known 4020m/1570s effort", () => {
    // Cross-checked the same way: compute_vdot(4020, 1570) == 27.578627416823142.
    expect(vdotForEffort(4020, 1570)).toBeCloseTo(27.578627416823142, 9);
  });

  it("returns null for non-positive inputs", () => {
    expect(vdotForEffort(0, 1500)).toBeNull();
    expect(vdotForEffort(5000, 0)).toBeNull();
    expect(vdotForEffort(-100, 1500)).toBeNull();
  });

  it("returns null for an effort too short for the aerobic model (%VO2max > 1)", () => {
    // A 1000m in 150s (2:30) is far too fast/short an effort for this model -- matches
    // vdot.py's own test_vdot.py::compute_vdot(1000, 150) is None.
    expect(vdotForEffort(1000, 150)).toBeNull();
  });

  it("a faster time over the same distance produces a higher VDOT", () => {
    const fast = vdotForEffort(5000, 1500)!;
    const slow = vdotForEffort(5000, 1800)!;
    expect(fast).toBeGreaterThan(slow);
  });
});

describe("predictRaceTimeS", () => {
  it("round-trips a VDOT computed from a real effort back to (approximately) the same duration", () => {
    const durationS = 1500;
    const vdot = vdotForEffort(5000, durationS)!;
    const predicted = predictRaceTimeS(vdot, 5000)!;
    expect(predicted).toBeCloseTo(durationS, 0);
  });

  it("round-trips for a different distance/duration pair", () => {
    const durationS = 2760; // 46:00
    const vdot = vdotForEffort(10000, durationS)!;
    const predicted = predictRaceTimeS(vdot, 10000)!;
    expect(predicted).toBeCloseTo(durationS, 0);
  });

  it("a higher VDOT predicts a faster (shorter) time at the same distance", () => {
    const slowerTime = predictRaceTimeS(35, 5000)!;
    const fasterTime = predictRaceTimeS(45, 5000)!;
    expect(fasterTime).toBeLessThan(slowerTime);
  });

  it("returns null for non-positive inputs", () => {
    expect(predictRaceTimeS(0, 5000)).toBeNull();
    expect(predictRaceTimeS(40, 0)).toBeNull();
    expect(predictRaceTimeS(-5, 5000)).toBeNull();
  });
});
