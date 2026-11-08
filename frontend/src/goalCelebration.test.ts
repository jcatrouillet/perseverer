import { beforeEach, describe, expect, it } from "vitest";

import type {
  BoulderingGoalProgressOut,
  DurationGoalProgressOut,
  GoalProgressOut,
} from "./api/types";
import { loadCelebrated, reachedGoals, saveCelebrated, unseen } from "./goalCelebration";

const km = (m: number) => `${Math.round(m / 1000)} km`;

function distance(id: number, pct: number | null): GoalProgressOut {
  return {
    available: true,
    goal: {
      id,
      period_type: "month",
      period_start: "2026-11",
      sport: "running",
      target_distance_m: 100000,
    },
    period_end: "2026-11-30",
    daily: [],
    target_per_day_m: null,
    current_distance_m: null,
    target_distance_as_of_today_m: null,
    ahead_behind_m: null,
    pct_complete: pct,
  };
}

function duration(id: number, pct: number): DurationGoalProgressOut {
  return {
    goal: { id, period_type: "year", period_start: "2026", sport: null, target_duration_s: 144000 },
    period_end: "2026-12-31",
    daily: [],
    target_per_day_s: 0,
    current_duration_s: 0,
    target_as_of_today_s: 0,
    ahead_behind_s: 0,
    pct_complete: pct,
  };
}

function bouldering(id: number, pct: number): BoulderingGoalProgressOut {
  return {
    goal: {
      id,
      period_type: "month",
      period_start: "2026-11",
      grade: 4,
      and_harder: true,
      target_count: 20,
    },
    period_end: "2026-11-30",
    daily: [],
    target_per_day: 0,
    current_count: 0,
    target_as_of_today: 0,
    ahead_behind: 0,
    pct_complete: pct,
  };
}

describe("goalCelebration", () => {
  beforeEach(() => window.localStorage.clear());

  it("keeps only the goals at 100% or more, titled by kind", () => {
    const reached = reachedGoals(
      "month",
      [distance(1, 1.02), distance(2, 0.99), distance(3, null)],
      [duration(4, 1)],
      [bouldering(5, 1.5), bouldering(6, 0.2)],
      km,
    );
    expect(reached.map((g) => [g.key, g.title])).toEqual([
      ["distance:1", "100 km running"],
      ["duration:4", "40 h all sports"],
      ["bouldering:5", "20 routes V4+"],
    ]);
  });

  it("congratulates once: a stored goal is no longer unseen", () => {
    const reached = reachedGoals("month", [distance(1, 1)], [], [], km);
    expect(unseen(reached, loadCelebrated())).toHaveLength(1);
    saveCelebrated(reached.map((g) => g.key));
    expect(unseen(reached, loadCelebrated())).toHaveLength(0);
  });

  it("survives unreadable storage", () => {
    window.localStorage.setItem("perseverer_celebrated_goals", "not json");
    expect(loadCelebrated().size).toBe(0);
  });
});
