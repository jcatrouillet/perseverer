import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LapOut } from "../api/types";
import { ActivityTrimControls } from "./ActivityTrimControls";
import type { RouteData } from "./ActivityRoute";

const ACTIVITY_START = "2025-06-01T08:00:00Z";

function route(n = 100, rawElapsedS?: number[]): RouteData {
  return {
    points: Array.from({ length: n }, (_, i) => ({ lat: 37 + i * 0.0001, lon: -122 })),
    distanceM: Array.from({ length: n }, (_, i) => i * 2),
    elapsedS: Array.from({ length: n }, (_, i) => i),
    rawElapsedS: rawElapsedS ?? Array.from({ length: n }, (_, i) => i),
    altitudeM: Array.from({ length: n }, (_, i) => 100 + i * 0.1),
  };
}

function lap(overrides: Partial<LapOut> = {}): LapOut {
  return {
    lap_index: 0,
    start_time_utc: ACTIVITY_START,
    duration_s: 50.0,
    moving_duration_s: null,
    distance_m: 100.0,
    avg_hr: null,
    max_hr: null,
    avg_speed_mps: null,
    avg_gap_speed_mps: null,
    ...overrides,
  };
}

const noop = () => {};

describe("ActivityTrimControls", () => {
  it("mounts and shows the kept duration for the initial (suggested) window", () => {
    render(
      <ActivityTrimControls
        route={route()}
        laps={[]}
        activityStartTimeUtc={ACTIVITY_START}
        suggestedTrimStartS={0}
        suggestedTrimEndS={99}
        onCommit={noop}
        onCancel={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.getByText(/Kept duration/)).toBeInTheDocument();
  });

  it("marks a lap outside the kept window as dropped, one inside as kept", () => {
    render(
      <ActivityTrimControls
        route={route()}
        laps={[
          lap({ lap_index: 0, start_time_utc: "2025-06-01T08:00:00Z", duration_s: 10 }), // [0,10)
          lap({ lap_index: 1, start_time_utc: "2025-06-01T08:01:00Z", duration_s: 10 }), // [60,70)
        ]}
        activityStartTimeUtc={ACTIVITY_START}
        suggestedTrimStartS={50}
        suggestedTrimEndS={99}
        onCommit={noop}
        onCancel={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.getByText("Lap 1 (dropped)")).toBeInTheDocument();
    expect(screen.getByText("Lap 2")).toBeInTheDocument();
  });

  it("calls onCommit with the current slider values, using null for an untouched boundary", () => {
    const onCommit = vi.fn();
    render(
      <ActivityTrimControls
        route={route()}
        laps={[]}
        activityStartTimeUtc={ACTIVITY_START}
        suggestedTrimStartS={0}
        suggestedTrimEndS={99}
        onCommit={onCommit}
        onCancel={noop}
        isSaving={false}
        isError={false}
      />,
    );

    fireEvent.click(screen.getByText("Save trim"));
    // Neither slider moved off its full-range default -- both trim bounds are "untrimmed".
    expect(onCommit).toHaveBeenCalledWith(null, null);
  });

  it("passes a real trim_start_s once the start slider moves", () => {
    const onCommit = vi.fn();
    render(
      <ActivityTrimControls
        route={route()}
        laps={[]}
        activityStartTimeUtc={ACTIVITY_START}
        suggestedTrimStartS={0}
        suggestedTrimEndS={99}
        onCommit={onCommit}
        onCancel={noop}
        isSaving={false}
        isError={false}
      />,
    );

    const sliders = screen.getAllByRole("slider");
    fireEvent.change(sliders[0]!, { target: { value: "30" } });
    fireEvent.click(screen.getByText("Save trim"));
    expect(onCommit).toHaveBeenCalledWith(30, null);
  });

  it("commits raw (uncompressed) elapsed seconds, not the compressed slider value, for a paused activity", () => {
    // A real device pause makes elapsedS (compressed, used for the slider domain) diverge from
    // rawElapsedS (real wall-clock seconds, what the backend's trim endpoint actually expects) --
    // e.g. a 1000s pause after index 30 collapses index 31 onwards way down in elapsedS while
    // rawElapsedS keeps counting real seconds. Regression test for the bug where onCommit sent
    // the compressed slider value straight through, silently committing a much shorter/earlier
    // window than the slider promised (confirmed against a real production activity).
    const rawElapsedS = Array.from({ length: 100 }, (_, i) => (i <= 30 ? i : i + 1000));
    const onCommit = vi.fn();
    render(
      <ActivityTrimControls
        route={route(100, rawElapsedS)}
        laps={[]}
        activityStartTimeUtc={ACTIVITY_START}
        suggestedTrimStartS={0}
        suggestedTrimEndS={99}
        onCommit={onCommit}
        onCancel={noop}
        isSaving={false}
        isError={false}
      />,
    );

    const sliders = screen.getAllByRole("slider");
    // Move the "Keep until" slider to compressed index 40 -- rawElapsedS there is 1040, not 40.
    fireEvent.change(sliders[1]!, { target: { value: "40" } });
    fireEvent.click(screen.getByText("Save trim"));
    expect(onCommit).toHaveBeenCalledWith(null, 1040);
  });

  it("calls onCancel when Cancel is clicked", () => {
    const onCancel = vi.fn();
    render(
      <ActivityTrimControls
        route={route()}
        laps={[]}
        activityStartTimeUtc={ACTIVITY_START}
        suggestedTrimStartS={0}
        suggestedTrimEndS={99}
        onCommit={noop}
        onCancel={onCancel}
        isSaving={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Cancel"));
    expect(onCancel).toHaveBeenCalled();
  });

  it("disables Save trim while isSaving", () => {
    render(
      <ActivityTrimControls
        route={route()}
        laps={[]}
        activityStartTimeUtc={ACTIVITY_START}
        suggestedTrimStartS={0}
        suggestedTrimEndS={99}
        onCommit={noop}
        onCancel={noop}
        isSaving={true}
        isError={false}
      />,
    );
    expect(screen.getByText("Saving…")).toBeDisabled();
  });

  it("shows an error message when isError", () => {
    render(
      <ActivityTrimControls
        route={route()}
        laps={[]}
        activityStartTimeUtc={ACTIVITY_START}
        suggestedTrimStartS={0}
        suggestedTrimEndS={99}
        onCommit={noop}
        onCancel={noop}
        isSaving={false}
        isError={true}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/Could not save/);
  });
});
