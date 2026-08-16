// buildSegments() unit tests: the pure chunking + speed-percentile + colour-lerp math behind
// ActivityRouteMap's pace gradient. Not a full render test (Leaflet needs a real DOM container
// size to lay out tiles, which jsdom doesn't provide) -- this covers the actual risk, which is
// the segment/colour math, not react-leaflet's own rendering.
import { describe, expect, it } from "vitest";

import { buildSegments, type RoutePoint } from "./ActivityRouteMap";

const RED: [number, number, number] = [255, 0, 0];
const BLUE: [number, number, number] = [0, 0, 255];

function points(n: number): RoutePoint[] {
  return Array.from({ length: n }, (_, i) => ({ lat: i * 0.001, lon: 0 }));
}

describe("buildSegments", () => {
  it("returns no segments for fewer than 2 points", () => {
    expect(buildSegments([], [], [], RED, BLUE)).toEqual([]);
    expect(buildSegments(points(1), [0], [0], RED, BLUE)).toEqual([]);
  });

  it("colours a fast leg toward `fastRgb` and a slow leg toward `slowRgb`", () => {
    // 5 legs, speeds [10, 1, 1, 1, 10] m/s -- the repeated extremes give the 5th/95th
    // percentile split (buildSegments' actual thresholds) a real min and max to land on,
    // rather than collapsing to a single value the way a 2-leg sample would.
    const distanceM = [0, 100, 110, 120, 130, 230];
    const elapsedS = [0, 10, 20, 30, 40, 50];
    const segs = buildSegments(points(6), distanceM, elapsedS, RED /* slow */, BLUE /* fast */);

    expect(segs).toHaveLength(5);
    expect(segs[0]!.color).toBe("rgb(0, 0, 255)"); // 10 m/s -> pure fastRgb
    expect(segs[1]!.color).toBe("rgb(255, 0, 0)"); // 1 m/s -> pure slowRgb
    expect(segs[4]!.color).toBe("rgb(0, 0, 255)"); // 10 m/s -> pure fastRgb
  });

  it("treats a near-stationary leg (below the moving-speed floor) as slow regardless of range", () => {
    // Same [10, 1, 1, 1, 10] moving-speed pattern as above (so slow/fast aren't collapsed to a
    // single value), preceded by a GPS auto-pause leg (0.01 m/s, below the 0.3 m/s floor).
    const distanceM = [0, 0.1, 100.1, 110.1, 120.1, 130.1, 230.1];
    const elapsedS = [0, 10, 20, 30, 40, 50, 60];
    const segs = buildSegments(points(7), distanceM, elapsedS, RED /* slow */, BLUE /* fast */);

    expect(segs[0]!.color).toBe("rgb(255, 0, 0)"); // stationary leg -> slowRgb, not fastRgb
    expect(segs[1]!.color).toBe("rgb(0, 0, 255)"); // genuinely fast leg right after it
  });
});
