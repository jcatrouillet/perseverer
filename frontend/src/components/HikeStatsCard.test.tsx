import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ActivityLocationOut, ActivitySummary } from "../api/types";
import { HikeStatsCard } from "./HikeStatsCard";

const mockUseActivityLocation = vi.fn();

vi.mock("../api/queries", () => ({
  useActivityLocation: (...args: unknown[]) => mockUseActivityLocation(...args),
}));

function hike(id: string, overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    id,
    start_time_utc: "2026-06-15T14:00:00Z",
    utc_offset_s: 0,
    local_date: "2026-06-15",
    sport: "hiking",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 7200,
    moving_duration_s: 7200,
    distance_m: 10000,
    elevation_gain_m: 400,
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
    ...overrides,
  };
}

const NO_LOCATION: ActivityLocationOut = { available: false, location_name: null };

describe("HikeStatsCard", () => {
  beforeEach(() => {
    mockUseActivityLocation.mockReturnValue({ data: NO_LOCATION });
  });

  it("renders nothing when there are no hikes in the period", () => {
    const { container } = render(<HikeStatsCard activities={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows count, total distance, and total time across all hikes", () => {
    const activities = [
      hike("h1", { distance_m: 10000, moving_duration_s: 7200 }),
      hike("h2", { distance_m: 5000, moving_duration_s: 3600 }),
    ];
    render(<HikeStatsCard activities={activities} />);
    expect(screen.getByText("2")).toBeInTheDocument(); // hike count
    expect(screen.getByText("15.0")).toBeInTheDocument(); // 15km total
    expect(screen.getByText("3h 0m")).toBeInTheDocument(); // 2h + 1h
  });

  it("features one card per criterion when three different hikes win", () => {
    const activities = [
      hike("longest-distance", { distance_m: 20000, moving_duration_s: 3600, elevation_gain_m: 100 }),
      hike("longest-time", { distance_m: 5000, moving_duration_s: 20000, elevation_gain_m: 100 }),
      hike("most-elevation", { distance_m: 5000, moving_duration_s: 3600, elevation_gain_m: 1500 }),
    ];
    render(<HikeStatsCard activities={activities} />);
    expect(screen.getByText("Longest hike")).toBeInTheDocument();
    expect(screen.getByText("Longest hike by time")).toBeInTheDocument();
    expect(screen.getByText("Highest elevation gain")).toBeInTheDocument();
  });

  it("skips a criterion when the same activity already won a different one", () => {
    // One dominant hike wins on both distance and time; a second, unremarkable hike exists too.
    const activities = [
      hike("dominant", { distance_m: 20000, moving_duration_s: 20000, elevation_gain_m: 0 }),
      hike("other", { distance_m: 1000, moving_duration_s: 600, elevation_gain_m: 0 }),
    ];
    render(<HikeStatsCard activities={activities} />);
    expect(screen.getByText("Longest hike")).toBeInTheDocument();
    expect(screen.queryByText("Longest hike by time")).not.toBeInTheDocument();
    // Neither hike has elevation data, so that slot doesn't appear at all.
    expect(screen.queryByText("Highest elevation gain")).not.toBeInTheDocument();
  });

  it("omits the elevation-gain feature entirely when no hike has elevation data", () => {
    const activities = [hike("h1", { elevation_gain_m: null })];
    render(<HikeStatsCard activities={activities} />);
    expect(screen.queryByText("Highest elevation gain")).not.toBeInTheDocument();
  });

  it("links each featured hike to its activity detail page", () => {
    const activities = [hike("h1")];
    render(<HikeStatsCard activities={activities} />);
    const link = screen.getByText("Longest hike").closest("a");
    expect(link).toHaveAttribute("href", "/activities/h1");
  });

  it("shows the reverse-geocoded location when available", () => {
    mockUseActivityLocation.mockReturnValue({
      data: { available: true, location_name: "Yosemite National Park" },
    });
    const activities = [hike("h1")];
    render(<HikeStatsCard activities={activities} />);
    expect(screen.getByText(/Yosemite National Park/)).toBeInTheDocument();
  });

  it("falls back to a generic 'Hike' title when the activity has no real name", () => {
    const activities = [hike("h1", { name: "Hike", workout_name: null })];
    render(<HikeStatsCard activities={activities} />);
    expect(screen.getByText("Hike")).toBeInTheDocument();
  });

  it("shows average and max elevation gain, only over hikes that actually recorded it", () => {
    const activities = [
      hike("h1", { elevation_gain_m: 200 }),
      hike("h2", { elevation_gain_m: 600 }),
      hike("h3", { elevation_gain_m: null }), // no elevation data -- excluded from the average
    ];
    render(<HikeStatsCard activities={activities} />);
    expect(screen.getByText("Average elevation gain")).toBeInTheDocument();
    expect(screen.getByText("400")).toBeInTheDocument(); // (200 + 600) / 2, not / 3
    expect(screen.getByText("Max elevation gain")).toBeInTheDocument();
    expect(screen.getByText("600")).toBeInTheDocument();
  });

  it("links the max elevation gain tile to that specific hike", () => {
    const activities = [
      hike("low", { elevation_gain_m: 100 }),
      hike("high", { elevation_gain_m: 900 }),
    ];
    render(<HikeStatsCard activities={activities} />);
    const link = screen.getByText("Max elevation gain").closest("a");
    expect(link).toHaveAttribute("href", "/activities/high");
  });

  it("omits both elevation stat tiles when no hike has elevation data", () => {
    const activities = [hike("h1", { elevation_gain_m: null })];
    render(<HikeStatsCard activities={activities} />);
    expect(screen.queryByText("Average elevation gain")).not.toBeInTheDocument();
    expect(screen.queryByText("Max elevation gain")).not.toBeInTheDocument();
  });
});
