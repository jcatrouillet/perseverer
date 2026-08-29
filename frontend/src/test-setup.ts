import { cleanup } from "@testing-library/react";
import * as L from "leaflet";
import { afterEach, vi } from "vitest";

import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
});

// @maplibre/maplibre-gl-leaflet constructs a real maplibre-gl WebGL Map on layer add
// (see CartoBasemapLayer.tsx), which needs a real WebGL canvas context jsdom simply doesn't
// provide -- there's no meaningful way to unit-test actual GL rendering here anyway (same
// "real network/real canvas, browser-verified instead" split mapTiles.test.ts's own docstring
// documents for the raster tile fetcher). Stand in a no-op Leaflet layer so every test that
// mounts a map (DayViewActivityRoute.test.tsx et al.) can still mount/unmount it without
// crashing, without asserting anything about its visual output.
vi.mock("@maplibre/maplibre-gl-leaflet", () => ({
  maplibreGL: () =>
    new (L.Layer.extend({
      onAdd() {},
      onRemove() {},
      getAttribution: () => "",
      getMaplibreMap: () => null,
    }))(),
}));

// jsdom has no layout engine, so every element reports a 0x0 bounding box and no
// ResizeObserver -- Recharts' <ResponsiveContainer> treats that as "nothing to render" and
// renders an empty <div>. Fake a real viewport so chart component tests (FitnessChart,
// ActivityCharts) can assert on actual rendered chart output, not an empty container.
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver ??=
  MockResizeObserver;

// Recharts reads the container's real size via getBoundingClientRect() synchronously on mount
// (see node_modules/recharts/lib/component/ResponsiveContainer.js), not offsetWidth/Height.
HTMLElement.prototype.getBoundingClientRect = () =>
  ({
    width: 800,
    height: 400,
    top: 0,
    left: 0,
    bottom: 400,
    right: 800,
    x: 0,
    y: 0,
    toJSON() {},
  }) as DOMRect;
