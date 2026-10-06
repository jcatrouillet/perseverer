import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityDetail, ActivityMetricOut, SplitOut } from "../api/types";
import { ActivityStatsGrid } from "./ActivityStatsGrid";

function metric(metric_key: string, value_num: number): ActivityMetricOut {
  return { metric_key, value_num, value_text: null, unit: null, source: "test" };
}

function climbSplit(overrides: Partial<SplitOut> = {}): SplitOut {
  return {
    split_index: 0,
    split_type: "climb_active",
    start_time_utc: null,
    end_time_utc: null,
    duration_s: 60,
    distance_m: null,
    climb_grade: 2,
    climb_result: "completed",
    climb_avg_hr: 100,
    climb_max_hr: 120,
    is_manual: null,
    ...overrides,
  };
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
    is_race: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m: 5000,
    elevation_gain_m: null,
    max_altitude_m: null,
    calories: null,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    vdot: null,
    workout_name: null,
    primary_source: "test",
    stream_available: false,
    climb_route_count: null,
    climb_max_completed_grade: null,
    climb_time_s: null,
    device: null,
    laps: [],
    splits: [],
    route: null,
    metrics: [],
    estimated_sweat_loss_ml: null,
    carbohydrates_g: null,
    sodium_mg: null,
    transport_mix_flag: null,
    has_trim: false,
    duplicate_candidates: [],
    ...overrides,
  };
}

describe("ActivityStatsGrid", () => {
  it("always shows distance and time for a plain activity", () => {
    render(<ActivityStatsGrid activity={activity()} />);
    expect(screen.getByText("Distance & time")).toBeInTheDocument();
    expect(screen.getByText("5.00")).toBeInTheDocument();
  });

  it("shows a VDOT tile under Training effect when present", () => {
    render(<ActivityStatsGrid activity={activity({ vdot: 41.2 })} />);
    expect(screen.getByText("Training effect")).toBeInTheDocument();
    expect(screen.getByText("VDOT")).toBeInTheDocument();
    expect(screen.getByText("41.2")).toBeInTheDocument();
  });

  it("omits sections with no backing data", () => {
    render(<ActivityStatsGrid activity={activity()} />);
    expect(screen.queryByText("Power")).not.toBeInTheDocument();
    expect(screen.queryByText("Running dynamics")).not.toBeInTheDocument();
    expect(screen.queryByText("Temperature")).not.toBeInTheDocument();
    expect(screen.queryByText("Elevation")).not.toBeInTheDocument();
    expect(screen.queryByText("Respiration")).not.toBeInTheDocument();
    expect(screen.queryByText("Hydration")).not.toBeInTheDocument();
  });

  it("omits avg pace/speed for a real 0m-distance activity rather than showing 0.0 km/h", () => {
    render(
      <ActivityStatsGrid
        activity={activity({
          sport: "strength_training",
          distance_m: 0,
          duration_s: 1800,
          moving_duration_s: 1800,
        })}
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

  it("doubles the raw max_running_cadence the same way as the avg", () => {
    render(
      <ActivityStatsGrid
        activity={activity({ metrics: [metric("fit.session.max_running_cadence", 88)] })}
      />,
    );
    expect(screen.getByText("Running dynamics")).toBeInTheDocument();
    expect(screen.getByText("Max cadence")).toBeInTheDocument();
    expect(screen.getByText("176")).toBeInTheDocument();
  });

  it("shows Respiration only when a respiration metric is actually present, e.g. a yoga session", () => {
    render(
      <ActivityStatsGrid
        activity={activity({
          sport: "training",
          sub_sport: "yoga",
          metrics: [
            metric("fit.session.enhanced_avg_respiration_rate", 14),
            metric("fit.session.enhanced_max_respiration_rate", 22),
          ],
        })}
      />,
    );
    expect(screen.getByText("Respiration")).toBeInTheDocument();
    expect(screen.getByText("Avg respiration")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("Max respiration")).toBeInTheDocument();
    expect(screen.getByText("22")).toBeInTheDocument();
  });

  it("shows Hydration with the estimated sweat loss converted to litres, only when present", () => {
    const { rerender } = render(<ActivityStatsGrid activity={activity()} />);
    expect(screen.queryByText("Hydration")).not.toBeInTheDocument();

    rerender(<ActivityStatsGrid activity={activity({ estimated_sweat_loss_ml: 861 })} />);
    expect(screen.getByText("Hydration")).toBeInTheDocument();
    expect(screen.getByText("0.86")).toBeInTheDocument();
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

  it("shows Max elevation with the peak altitude, alongside gain/loss", () => {
    render(
      <ActivityStatsGrid
        activity={activity({
          elevation_gain_m: 120,
          max_altitude_m: 2690,
          metrics: [metric("fit.session.total_descent", 90)],
        })}
      />,
    );
    expect(screen.getByText("Max elevation")).toBeInTheDocument();
    expect(screen.getByText("2690")).toBeInTheDocument();
  });

  it("shows an elevation section from max_altitude_m alone, even with no gain/loss", () => {
    render(<ActivityStatsGrid activity={activity({ max_altitude_m: 1500 })} />);
    expect(screen.getByText("Elevation")).toBeInTheDocument();
    expect(screen.getByText("Max elevation")).toBeInTheDocument();
    expect(screen.queryByText("Elevation gain")).not.toBeInTheDocument();
    expect(screen.queryByText("Elevation loss")).not.toBeInTheDocument();
  });

  it("gives Heart rate and Elevation their own separate headings, not a combined one", () => {
    render(
      <ActivityStatsGrid
        activity={activity({
          avg_hr_bpm: 140,
          elevation_gain_m: 120,
        })}
      />,
    );
    expect(screen.getByText("Heart rate", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Elevation", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("Heart rate & Elevation")).not.toBeInTheDocument();
    expect(screen.getByText("Elevation gain")).toBeInTheDocument();
  });

  it("renders afterTemperature content between Temperature and Hydration", () => {
    render(
      <ActivityStatsGrid
        activity={activity({
          metrics: [metric("fit.session.avg_temperature", 18)],
          estimated_sweat_loss_ml: 500,
        })}
        afterTemperature={<h3>Pace variability</h3>}
      />,
    );
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    const tempIndex = headings.indexOf("Temperature");
    const slotIndex = headings.indexOf("Pace variability");
    const hydrationIndex = headings.indexOf("Hydration");
    expect(tempIndex).toBeGreaterThanOrEqual(0);
    expect(slotIndex).toBeGreaterThan(tempIndex);
    expect(hydrationIndex).toBeGreaterThan(slotIndex);
  });

  it("renames Distance & time to Time & calories for a bouldering activity", () => {
    render(
      <ActivityStatsGrid
        activity={activity({
          sport: "rock_climbing",
          sub_sport: "bouldering",
          distance_m: null,
          splits: [climbSplit()],
        })}
      />,
    );
    expect(screen.getByText("Time & calories")).toBeInTheDocument();
    expect(screen.queryByText("Distance & time")).not.toBeInTheDocument();
  });

  it("shows a Climb section with max completed grade, route count, and climb time", () => {
    render(
      <ActivityStatsGrid
        activity={activity({
          sport: "rock_climbing",
          sub_sport: "bouldering",
          distance_m: null,
          splits: [
            climbSplit({
              split_index: 0,
              climb_grade: 2,
              climb_result: "completed",
              duration_s: 60,
            }),
            climbSplit({ split_index: 2, climb_grade: 4, climb_result: "attempt", duration_s: 90 }),
          ],
        })}
      />,
    );
    expect(screen.getByText("Climb")).toBeInTheDocument();
    expect(screen.getByText("V2")).toBeInTheDocument(); // max *completed* grade, not the V4 attempt
    expect(screen.getByText("2")).toBeInTheDocument(); // route count
  });

  it("shows no Climb section for a non-bouldering activity", () => {
    render(<ActivityStatsGrid activity={activity()} />);
    expect(screen.queryByText("Climb")).not.toBeInTheDocument();
  });
});
