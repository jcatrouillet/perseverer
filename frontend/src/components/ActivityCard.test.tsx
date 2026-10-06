import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "../api/types";
import { ActivityCard } from "./ActivityCard";

function activity(overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    id: "act1",
    start_time_utc: "2025-06-01T13:00:00Z",
    utc_offset_s: -25200, // -7h: 06:00 local
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
    ...overrides,
  };
}

describe("ActivityCard", () => {
  it("links to the activity detail page", () => {
    render(<ActivityCard activity={activity({ id: "abc123" })} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/activities/abc123");
  });

  it("shows a race badge only when is_race is true", () => {
    const { rerender } = render(<ActivityCard activity={activity({ is_race: false })} />);
    expect(screen.queryByText("Race")).not.toBeInTheDocument();

    rerender(<ActivityCard activity={activity({ is_race: null })} />);
    expect(screen.queryByText("Race")).not.toBeInTheDocument();

    rerender(<ActivityCard activity={activity({ is_race: true })} />);
    expect(screen.getByText("Race")).toBeInTheDocument();
  });

  it("shows the sport, distance, duration and pace for a plain run", () => {
    render(<ActivityCard activity={activity()} />);
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("5.00 km")).toBeInTheDocument();
    expect(screen.getByText("30m")).toBeInTheDocument();
    // 30:00 for 5km -> 6:00/km.
    expect(screen.getByText("6:00 /km")).toBeInTheDocument();
    // 24h by default (Personalize's own stated default) -- not "6:00 AM".
    expect(screen.getByText("06:00")).toBeInTheDocument();
  });

  it("shows the activity name when present, and omits it when absent", () => {
    const { rerender } = render(<ActivityCard activity={activity({ name: "Morning loop" })} />);
    expect(screen.getByText("Morning loop")).toBeInTheDocument();

    rerender(<ActivityCard activity={activity({ name: null })} />);
    expect(screen.queryByText("Morning loop")).not.toBeInTheDocument();
  });

  it("shows heart rate, load and RPE only when the API actually returned them", () => {
    const { rerender } = render(<ActivityCard activity={activity()} />);
    expect(screen.queryByText(/bpm/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Load/)).not.toBeInTheDocument();
    expect(screen.queryByText(/RPE/)).not.toBeInTheDocument();

    rerender(
      <ActivityCard
        activity={activity({ avg_hr_bpm: 142, training_load: 81.8, workout_rpe: 4.6 })}
      />,
    );
    expect(screen.getByText("142 bpm")).toBeInTheDocument();
    expect(screen.getByText("Load 82")).toBeInTheDocument();
    expect(screen.getByText("RPE 4.6")).toBeInTheDocument();
  });

  it("shows speed (km/h) rather than pace for a wheeled sport", () => {
    render(
      <ActivityCard
        activity={activity({
          sport: "cycling",
          distance_m: 20000,
          duration_s: 3600,
          moving_duration_s: 3600,
        })}
      />,
    );
    expect(screen.getByText("20.0 km/h")).toBeInTheDocument();
    expect(screen.queryByText(/\/km/)).not.toBeInTheDocument();
  });

  it("omits the pace/speed chip for a real 0m-distance activity (e.g. strength training) rather than showing a meaningless 0.0 km/h", () => {
    render(
      <ActivityCard
        activity={activity({
          sport: "strength_training",
          distance_m: 0,
          duration_s: 1800,
          moving_duration_s: 1800,
        })}
      />,
    );
    expect(screen.queryByText(/km\/h/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\/km/)).not.toBeInTheDocument();
  });

  it("substitutes sub_sport for the generic 'training' container, matching the type list elsewhere", () => {
    render(<ActivityCard activity={activity({ sport: "training", sub_sport: "yoga" })} />);
    expect(screen.getByText("yoga")).toBeInTheDocument();
  });

  it("renders the default-size icon chip unless iconSize is explicitly 'large'", () => {
    const { container, rerender } = render(<ActivityCard activity={activity()} />);
    expect(container.querySelector(".icon-chip--lg")).not.toBeInTheDocument();

    rerender(<ActivityCard activity={activity()} iconSize="large" />);
    expect(container.querySelector(".icon-chip--lg")).toBeInTheDocument();
  });

  it("shows the bouldering pill (routes, max grade, climb time) when present", () => {
    render(
      <ActivityCard
        activity={activity({
          sport: "rock_climbing",
          sub_sport: "bouldering",
          distance_m: null,
          climb_route_count: 19,
          climb_max_completed_grade: 4,
          climb_time_s: 3600,
        })}
      />,
    );
    expect(screen.getByText("19 routes")).toBeInTheDocument();
    expect(screen.getByText("V4")).toBeInTheDocument();
    expect(screen.getByText("1h 0m")).toBeInTheDocument();
  });

  it("shows no bouldering pill for a non-bouldering activity", () => {
    render(<ActivityCard activity={activity()} />);
    expect(screen.queryByText(/routes?$/)).not.toBeInTheDocument();
  });
});
