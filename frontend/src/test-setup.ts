import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
});

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
