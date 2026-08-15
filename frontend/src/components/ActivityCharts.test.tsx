import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LapOut, StreamResponse } from "../api/types";
import { ActivityCharts } from "./ActivityCharts";

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
});
