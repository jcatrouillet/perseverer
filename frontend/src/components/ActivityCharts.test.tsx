import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityWorkoutStepOut, LapOut, StreamResponse } from "../api/types";
import { ActivityCharts } from "./ActivityCharts";

function workoutStep(overrides: Partial<ActivityWorkoutStepOut>): ActivityWorkoutStepOut {
  return {
    step_index: 0,
    duration_type: null,
    duration_time_s: null,
    duration_distance_m: null,
    target_type: null,
    target_low_mps: null,
    target_high_mps: null,
    intensity: null,
    repeat_from_step: null,
    repeat_count: null,
    ...overrides,
  };
}

function stream(overrides: Partial<StreamResponse> = {}): StreamResponse {
  const timestamps = ["2025-06-01T08:00:00Z", "2025-06-01T08:00:10Z", "2025-06-01T08:00:20Z"];
  return {
    activity_id: "a1",
    tier: "medium",
    channels: ["heart_rate", "altitude_m"],
    timestamps,
    series: {
      heart_rate: [120, 130, 140],
      altitude_m: [10, 12, 14],
    },
    ...overrides,
  };
}

function lap(start_time_utc: string, index: number): LapOut {
  return {
    lap_index: index,
    start_time_utc,
    duration_s: 300,
    moving_duration_s: 300,
    distance_m: 1000,
    avg_hr: 130,
    max_hr: 145,
    avg_speed_mps: 3.3,
  };
}

describe("ActivityCharts", () => {
  it("renders one panel per channel the activity actually has, and nothing else", () => {
    render(<ActivityCharts stream={stream()} laps={[]} sport="running" />);
    expect(screen.getByText("Heart rate")).toBeInTheDocument();
    expect(screen.getByText("Elevation")).toBeInTheDocument();
    // No power, cadence, temperature, or speed channel in this fixture.
    expect(screen.queryByText("Power")).not.toBeInTheDocument();
    expect(screen.queryByText("Cadence")).not.toBeInTheDocument();
    expect(screen.queryByText("Temperature")).not.toBeInTheDocument();
    expect(screen.queryByText("Pace")).not.toBeInTheDocument();
    expect(screen.queryByText("Grade Adjusted Pace")).not.toBeInTheDocument();
  });

  it("shows a Respiration panel when the activity has a respiration_rate stream, e.g. yoga", () => {
    const withRespiration = stream({
      channels: ["heart_rate", "respiration_rate"],
      series: { heart_rate: [80, 82, 81], respiration_rate: [14, 15, 14] },
    });
    render(<ActivityCharts stream={withRespiration} laps={[]} sport="training" />);
    expect(screen.getByText("Respiration")).toBeInTheDocument();
  });

  it("labels the speed panel 'Pace' for a foot sport and 'Speed' for a wheeled one", () => {
    const withSpeed = stream({
      channels: ["speed_mps"],
      series: { speed_mps: [3.0, 3.2, 3.1] },
    });
    const { rerender } = render(<ActivityCharts stream={withSpeed} laps={[]} sport="running" />);
    expect(screen.getByText("Pace")).toBeInTheDocument();
    expect(screen.queryByText("Speed")).not.toBeInTheDocument();

    rerender(<ActivityCharts stream={withSpeed} laps={[]} sport="cycling" />);
    expect(screen.getByText("Speed")).toBeInTheDocument();
    expect(screen.queryByText("Pace")).not.toBeInTheDocument();
  });

  it("shows a Grade Adjusted Pace panel right after Pace for a foot sport with elevation data", () => {
    const withElevation = stream({
      channels: ["speed_mps", "distance_m", "altitude_m"],
      series: {
        speed_mps: [3.0, 3.0, 3.0],
        distance_m: [0, 30, 60],
        altitude_m: [0, 3, 6], // a steady steep-enough grade to survive the smoothing window
      },
    });
    const { container } = render(
      <ActivityCharts stream={withElevation} laps={[]} sport="running" />,
    );
    const titles = Array.from(container.querySelectorAll(".activity-charts__panel h4")).map(
      (h) => h.textContent,
    );
    const paceIdx = titles.findIndex((t) => t?.includes("Pace") && !t.includes("Grade"));
    const gapIdx = titles.findIndex((t) => t?.includes("Grade Adjusted Pace"));
    expect(paceIdx).toBeGreaterThanOrEqual(0);
    expect(gapIdx).toBe(paceIdx + 1);
  });

  it("does not show Grade Adjusted Pace for a wheeled sport even with elevation data", () => {
    const withElevation = stream({
      channels: ["speed_mps", "distance_m", "altitude_m"],
      series: {
        speed_mps: [5.0, 5.0, 5.0],
        distance_m: [0, 50, 100],
        altitude_m: [0, 5, 10],
      },
    });
    render(<ActivityCharts stream={withElevation} laps={[]} sport="cycling" />);
    expect(screen.getByText("Speed")).toBeInTheDocument();
    expect(screen.queryByText("Grade Adjusted Pace")).not.toBeInTheDocument();
  });

  it("shows a fallback message rather than an empty chart grid when no known channel is present", () => {
    const empty = stream({ channels: ["unrecognized_channel"], series: {} });
    render(<ActivityCharts stream={empty} laps={[]} sport="running" />);
    expect(screen.getByText(/no stream data available/i)).toBeInTheDocument();
  });

  it("does not render a panel whose only values are all null after conversion", () => {
    // Every speed sample is below the 0.3 m/s stationary floor -- streamSpeedValue nulls all
    // of them out for a foot sport, so the Pace panel must not appear.
    const stationary = stream({
      channels: ["speed_mps"],
      series: { speed_mps: [0.1, 0.05, 0] },
    });
    render(<ActivityCharts stream={stationary} laps={[]} sport="running" />);
    expect(screen.queryByText("Pace")).not.toBeInTheDocument();
  });

  it("draws a reference line for a lap boundary strictly inside the stream, not at t=0", () => {
    const s = stream();
    const laps = [lap("2025-06-01T08:00:00Z", 0), lap("2025-06-01T08:00:10Z", 1)];
    const { container } = render(<ActivityCharts stream={s} laps={laps} sport="running" />);
    // One real (non-zero) lap boundary across two synced panels (heart rate + elevation).
    expect(container.querySelectorAll(".recharts-reference-line")).toHaveLength(2);
  });

  it("shades each lap as a background band across every panel", () => {
    const s = stream();
    const laps = [lap("2025-06-01T08:00:00Z", 0), lap("2025-06-01T08:00:10Z", 1)];
    const { container } = render(<ActivityCharts stream={s} laps={laps} sport="running" />);
    // 3 boundaries (0, 10, maxT=20) -> 2 lap bands, but only the odd-indexed one is shaded ->
    // 1 shaded ReferenceArea per panel, across 2 synced panels (heart rate + elevation).
    expect(container.querySelectorAll(".recharts-reference-area")).toHaveLength(2);
  });

  it("draws a flat expected-pace reference line on the Pace panel at the activity's own average pace", () => {
    const withSpeed = stream({
      channels: ["speed_mps"],
      series: { speed_mps: [3.0, 3.2, 3.1] },
    });
    // distanceM/durationS chosen so the average pace (5:33 /km) differs from any raw sample.
    const { container } = render(
      <ActivityCharts stream={withSpeed} laps={[]} sport="running" distanceM={3000} durationS={1000} />,
    );
    const labels = Array.from(container.querySelectorAll(".recharts-label")).map((l) => l.textContent);
    expect(labels).toContain("Avg");
  });

  it("omits the expected-pace reference line when distance/duration aren't provided", () => {
    const withSpeed = stream({
      channels: ["speed_mps"],
      series: { speed_mps: [3.0, 3.2, 3.1] },
    });
    const { container } = render(<ActivityCharts stream={withSpeed} laps={[]} sport="running" />);
    const labels = Array.from(container.querySelectorAll(".recharts-label")).map((l) => l.textContent);
    expect(labels).not.toContain("Avg");
  });

  it("highlights the hovered lap's own time range across every panel, on top of the existing lap-band shading", () => {
    const s = stream();
    const laps = [lap("2025-06-01T08:00:00Z", 0), lap("2025-06-01T08:00:10Z", 1)];
    const withoutHighlight = render(
      <ActivityCharts stream={s} laps={laps} sport="running" highlightLapIndex={null} />,
    );
    const baseline = withoutHighlight.container.querySelectorAll(".recharts-reference-area").length;
    withoutHighlight.unmount();

    const { container } = render(
      <ActivityCharts stream={s} laps={laps} sport="running" highlightLapIndex={1} />,
    );
    // One extra ReferenceArea per synced panel (heart rate + elevation) beyond the baseline
    // lap-band shading, which is already present regardless of hover state.
    expect(container.querySelectorAll(".recharts-reference-area").length).toBe(baseline + 2);
  });

  it("shows the workout's target pace as a shaded step area on the Pace panel, with no on-chart text", () => {
    const withSpeed = stream({ channels: ["speed_mps"], series: { speed_mps: [3.0, 3.2, 3.1] } });
    const laps = [lap("2025-06-01T08:00:00Z", 0), lap("2025-06-01T08:00:10Z", 1)];
    const workout = {
      name: "Threshold",
      description: null,
      steps: [
        workoutStep({
          step_index: 0,
          duration_type: "time",
          duration_time_s: 10,
          target_type: "speed",
          target_low_mps: 2.439,
          target_high_mps: 2.597,
        }),
      ],
    };
    const { container } = render(
      <ActivityCharts stream={withSpeed} laps={laps} sport="running" workout={workout} />,
    );
    const pacePanel = Array.from(container.querySelectorAll(".activity-charts__panel")).find(
      (p) => p.querySelector("h4")?.textContent?.includes("Pace") && !p.querySelector("h4")?.textContent?.includes("Grade"),
    )!;
    expect(pacePanel.querySelectorAll(".recharts-area").length).toBeGreaterThan(0);
    const labels = Array.from(container.querySelectorAll(".recharts-label")).map((l) => l.textContent);
    expect(labels).not.toContain("6:25-6:50");
  });

  it("omits the workout overlay entirely when no workout is provided", () => {
    const withSpeed = stream({ channels: ["speed_mps"], series: { speed_mps: [3.0, 3.2, 3.1] } });
    const laps = [lap("2025-06-01T08:00:00Z", 0), lap("2025-06-01T08:00:10Z", 1)];
    const { container } = render(
      <ActivityCharts stream={withSpeed} laps={laps} sport="running" workout={null} />,
    );
    const pacePanel = Array.from(container.querySelectorAll(".activity-charts__panel")).find(
      (p) => p.querySelector("h4")?.textContent?.includes("Pace") && !p.querySelector("h4")?.textContent?.includes("Grade"),
    )!;
    expect(pacePanel.querySelectorAll(".recharts-area").length).toBe(0);
  });
});
