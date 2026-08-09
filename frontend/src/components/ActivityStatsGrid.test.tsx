import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityDetail, ActivityMetricOut } from "../api/types";
import { ActivityStatsGrid } from "./ActivityStatsGrid";

function metric(metric_key: string, value_num: number): ActivityMetricOut {
  return { metric_key, value_num, value_text: null, unit: null, source: "test" };
}

function activity(overrides: Partial<ActivityDetail> = {}): ActivityDetail {
  return {
    id: "a1",
    start_time_utc: "2025-06-01T08:00:00Z",
    utc_offset_s: 0,
    local_date: "2025-06-01",
    sport: "running",
    sub_sport: null,
    name: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m: 5000,
    elevation_gain_m: null,
    calories: null,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    primary_source: "test",
    stream_available: false,
    device: null,
    laps: [],
    splits: [],
    route: null,
    metrics: [],
    ...overrides,
  };
}

describe("ActivityStatsGrid", () => {
  it("always shows distance and time for a plain activity", () => {
    render(<ActivityStatsGrid activity={activity()} />);
    expect(screen.getByText("Distance & time")).toBeInTheDocument();
    expect(screen.getByText("5.00")).toBeInTheDocument();
  });

  it("omits sections with no backing data", () => {
    render(<ActivityStatsGrid activity={activity()} />);
    expect(screen.queryByText("Power")).not.toBeInTheDocument();
    expect(screen.queryByText("Running dynamics")).not.toBeInTheDocument();
    expect(screen.queryByText("Temperature")).not.toBeInTheDocument();
    expect(screen.queryByText("Elevation")).not.toBeInTheDocument();
  });

  it("omits avg pace/speed for a real 0m-distance activity rather than showing 0.0 km/h", () => {
    render(
      <ActivityStatsGrid
        activity={activity({ sport: "strength_training", distance_m: 0, duration_s: 1800, moving_duration_s: 1800 })}
      />,
    );
    expect(screen.queryByText("Avg speed")).not.toBeInTheDocument();
    expect(screen.queryByText("Avg pace")).not.toBeInTheDocument();
  });

  it("shows Power only when a power metric is actually present", () => {
    render(
      <ActivityStatsGrid
        activity={activity({ metrics: [metric("fit.session.avg_power", 220)] })}
      />,
    );
    expect(screen.getByText("Power")).toBeInTheDocument();
    expect(screen.getByText("220")).toBeInTheDocument();
  });

  it("doubles the raw avg_running_cadence to match Garmin's own displayed spm", () => {
    render(
      <ActivityStatsGrid
        activity={activity({ metrics: [metric("fit.session.avg_running_cadence", 83)] })}
      />,
    );
    expect(screen.getByText("Running dynamics")).toBeInTheDocument();
    expect(screen.getByText("166")).toBeInTheDocument();
  });

  it("shows an elevation section from total_descent alone, even with no elevation_gain_m", () => {
    render(
      <ActivityStatsGrid
        activity={activity({
          elevation_gain_m: null,
          metrics: [metric("fit.session.total_descent", 120)],
        })}
      />,
    );
    expect(screen.getByText("Elevation")).toBeInTheDocument();
    expect(screen.getByText("Elevation loss")).toBeInTheDocument();
  });
});
