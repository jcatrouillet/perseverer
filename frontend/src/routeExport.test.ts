// projectPoints() unit tests: the pure lat/lon -> canvas-coordinate fit-to-bounds projection
// behind the PNG route poster export. drawRoutePoster/renderRoutePosterBlob aren't covered here
// -- they need a real canvas 2D context and resolved CSS custom properties, which jsdom doesn't
// provide meaningfully; the actual risk (does the route's real shape survive projection) lives
// entirely in this pure function.
import { describe, expect, it } from "vitest";

import { projectPoints } from "./routeExport";
import type { RoutePoint } from "./components/ActivityRouteMap";

describe("projectPoints", () => {
  it("returns an empty array for no points", () => {
    expect(projectPoints([], 400, 400, 20)).toEqual([]);
  });

  it("keeps every projected point within the padded canvas bounds", () => {
    const points: RoutePoint[] = [
      { lat: 45.0, lon: -73.6 },
      { lat: 45.02, lon: -73.55 },
      { lat: 44.98, lon: -73.62 },
      { lat: 45.01, lon: -73.58 },
    ];
    const width = 400;
    const height = 300;
    const padding = 30;
    const projected = projectPoints(points, width, height, padding);

    expect(projected).toHaveLength(points.length);
    for (const p of projected) {
      expect(p.x).toBeGreaterThanOrEqual(padding - 1);
      expect(p.x).toBeLessThanOrEqual(width - padding + 1);
      expect(p.y).toBeGreaterThanOrEqual(padding - 1);
      expect(p.y).toBeLessThanOrEqual(height - padding + 1);
    }
  });

  it("maps north to a smaller y (canvas y grows downward, latitude grows northward)", () => {
    const points: RoutePoint[] = [
      { lat: 45.0, lon: -73.6 }, // south
      { lat: 45.1, lon: -73.6 }, // north
    ];
    const [south, north] = projectPoints(points, 400, 400, 20);
    expect(north!.y).toBeLessThan(south!.y);
  });

  it("preserves aspect ratio: a route twice as wide (in real metres) as it is tall renders twice as wide", () => {
    // At the equator (no cos(lat) distortion to account for), 0.02deg lon ~= 2x 0.01deg lat.
    const points: RoutePoint[] = [
      { lat: 0, lon: 0 },
      { lat: 0.01, lon: 0.02 },
    ];
    const projected = projectPoints(points, 400, 400, 0);
    const drawnW = Math.abs(projected[1]!.x - projected[0]!.x);
    const drawnH = Math.abs(projected[1]!.y - projected[0]!.y);
    expect(drawnW / drawnH).toBeCloseTo(2, 1);
  });
});
