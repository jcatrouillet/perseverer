// Pure Web Mercator math unit tests. drawBasemapTiles itself (tile fetch + canvas draw) isn't
// covered here -- it needs real network access and a real 2D canvas context neither jsdom nor
// a unit test should depend on; the real risk (does the projection math line up) lives entirely
// in these two pure functions, matching this project's established split between pure-logic
// unit tests and browser-verified rendering (see routeExport.test.ts's own docstring).
import { describe, expect, it } from "vitest";

import { chooseZoom, lonLatToWorldPixel } from "./mapTiles";

describe("lonLatToWorldPixel", () => {
  it("maps the antimeridian/equator corners to the world's own pixel edges at zoom 0", () => {
    expect(lonLatToWorldPixel(-180, 0, 0).x).toBeCloseTo(0, 5);
    expect(lonLatToWorldPixel(180, 0, 0).x).toBeCloseTo(256, 5);
    expect(lonLatToWorldPixel(0, 0, 0).x).toBeCloseTo(128, 5);
    expect(lonLatToWorldPixel(0, 0, 0).y).toBeCloseTo(128, 5);
  });

  it("doubles the world pixel size with every zoom level", () => {
    const z0 = lonLatToWorldPixel(180, 0, 0);
    const z1 = lonLatToWorldPixel(180, 0, 1);
    const z2 = lonLatToWorldPixel(180, 0, 2);
    expect(z1.x).toBeCloseTo(z0.x * 2, 5);
    expect(z2.x).toBeCloseTo(z0.x * 4, 5);
  });

  it("maps north to a smaller y (world pixel y grows southward, same convention as canvas)", () => {
    const north = lonLatToWorldPixel(0, 45, 5);
    const south = lonLatToWorldPixel(0, -45, 5);
    expect(north.y).toBeLessThan(south.y);
  });
});

describe("chooseZoom", () => {
  it("picks the maximum zoom for a tiny bounding box in a large viewport", () => {
    const zoom = chooseZoom(-73.61, 45.0, -73.6, 45.001, 2000, 2000);
    expect(zoom).toBe(17);
  });

  it("picks a low zoom for a huge bounding box in a small viewport", () => {
    const zoom = chooseZoom(-170, -80, 170, 80, 300, 300);
    expect(zoom).toBeLessThanOrEqual(2);
  });

  it("never returns a zoom below the documented minimum, even for an enormous box", () => {
    const zoom = chooseZoom(-180, -85, 180, 85, 50, 50);
    expect(zoom).toBeGreaterThanOrEqual(1);
  });

  it("picks a smaller (or equal) zoom for a smaller available viewport, same bounding box", () => {
    const bbox: [number, number, number, number] = [-73.62, 45.0, -73.58, 45.02];
    const zoomLarge = chooseZoom(...bbox, 1000, 1000);
    const zoomSmall = chooseZoom(...bbox, 200, 200);
    expect(zoomSmall).toBeLessThanOrEqual(zoomLarge);
  });
});
