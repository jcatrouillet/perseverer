import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { StreamResponse } from "../api/types";
import { DayViewActivityRoute } from "./DayViewActivityRoute";

const stream: StreamResponse = {
  activity_id: "a1",
  tier: "high",
  channels: ["lat", "lon"],
  timestamps: ["2025-06-01T08:00:00Z", "2025-06-01T08:00:10Z", "2025-06-01T08:00:20Z"],
  series: {
    lat: [45.0, 45.001, 45.002],
    lon: [-73.6, -73.601, -73.602],
  },
};

vi.mock("../api/queries", () => ({
  useActivityStream: () => ({ data: stream }),
}));

describe("DayViewActivityRoute", () => {
  it("mounts without crashing given a real multi-point stream", () => {
    // Leaflet's MapContainer needs a real layout engine to draw tiles, which jsdom doesn't
    // provide (see ActivityRouteMap.test.tsx's own docstring) -- this only proves the component
    // mounts and drives its own auto-play effect without throwing, not the map's visual output.
    const { container } = render(<DayViewActivityRoute activityId="a1" />);
    expect(container.querySelector(".day-view-route")).toBeInTheDocument();
  });

  it("plays the route animation once and stops -- no loop, no restart on re-render", () => {
    let now = 0;
    const state: { pendingFrame: FrameRequestCallback | null } = { pendingFrame: null };
    const rafSpy = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation((cb: FrameRequestCallback) => {
        state.pendingFrame = cb;
        return 1;
      });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    vi.spyOn(performance, "now").mockImplementation(() => now);

    const { rerender } = render(<DayViewActivityRoute activityId="a1" />);
    expect(rafSpy).toHaveBeenCalledTimes(1);

    // Drive frames well past ANIMATION_DURATION_MS (15s) -- each callback invocation schedules
    // its own next frame internally (mirroring real requestAnimationFrame usage) until progress
    // reaches 1, at which point the effect stops scheduling further frames.
    for (let i = 0; i < 20; i++) {
      now += 1000;
      state.pendingFrame?.(now);
    }
    const callsOnceFinished = rafSpy.mock.calls.length;

    now += 1000;
    state.pendingFrame?.(now);
    expect(rafSpy.mock.calls.length).toBe(callsOnceFinished); // finished: no further frame requested

    // Re-rendering the same instance must not restart the animation from the beginning --
    // hasPlayedRef guards the effect from re-running.
    rerender(<DayViewActivityRoute activityId="a1" />);
    expect(rafSpy.mock.calls.length).toBe(callsOnceFinished);

    vi.restoreAllMocks();
  });
});
