import { describe, expect, it } from "vitest";

import type { SplitOut } from "./api/types";
import { boulderingRoutes, climbSummary, formatGrade, formatResult, summarizeBoulderingRoutes } from "./boulderingRoutes";

function split(overrides: Partial<SplitOut> = {}): SplitOut {
  return {
    split_index: 0,
    split_type: "climb_active",
    start_time_utc: "2026-08-19T01:46:47Z",
    end_time_utc: "2026-08-19T01:47:38Z",
    duration_s: 51.6,
    distance_m: null,
    climb_grade: 2,
    climb_result: "completed",
    climb_avg_hr: 99,
    climb_max_hr: 132,
    is_manual: null,
    ...overrides,
  };
}

describe("boulderingRoutes", () => {
  it("keeps only climb_active splits, in order, numbered from 1", () => {
    const splits = [
      split({ split_index: 0, climb_grade: 2, climb_result: "completed" }),
      split({ split_index: 1, split_type: "climb_rest", climb_grade: null, climb_result: null }),
      split({ split_index: 2, climb_grade: 3, climb_result: "attempt" }),
    ];
    const routes = boulderingRoutes(splits);
    expect(routes.map((r) => r.routeNumber)).toEqual([1, 2]);
    expect(routes.map((r) => r.grade)).toEqual([2, 3]);
  });

  it("excludes a climb_active split with no grade (not real route data)", () => {
    const splits = [split({ climb_grade: null })];
    expect(boulderingRoutes(splits)).toEqual([]);
  });

  it("falls back to 'unknown' when the backend sent no result at all", () => {
    const routes = boulderingRoutes([split({ climb_result: null })]);
    expect(routes[0]!.result).toBe("unknown");
  });

  it("carries the climb's own avg/max HR through, not the rest interval's", () => {
    const routes = boulderingRoutes([split({ climb_avg_hr: 105, climb_max_hr: 140 })]);
    expect(routes[0]!.avgHr).toBe(105);
    expect(routes[0]!.maxHr).toBe(140);
  });
});

describe("formatGrade", () => {
  it("prefixes the V-scale number", () => {
    expect(formatGrade(0)).toBe("V0");
    expect(formatGrade(4)).toBe("V4");
  });
});

describe("formatResult", () => {
  it("titlecases the two known results", () => {
    expect(formatResult("completed")).toBe("Completed");
    expect(formatResult("attempt")).toBe("Attempt");
  });

  it("shows an unrecognized raw value honestly rather than guessing", () => {
    expect(formatResult("unknown_9")).toBe("unknown_9");
  });
});

describe("summarizeBoulderingRoutes", () => {
  it("counts completed routes out of the total", () => {
    const routes = boulderingRoutes([
      split({ split_index: 0, climb_result: "completed" }),
      split({ split_index: 2, climb_result: "attempt" }),
      split({ split_index: 4, climb_result: "completed" }),
    ]);
    expect(summarizeBoulderingRoutes(routes)).toEqual({ totalRoutes: 3, completedRoutes: 2 });
  });
});

describe("climbSummary with demoted Garmin rows", () => {
  it("counts a superseded Garmin row's duration toward climb time without making it a route", () => {
    const splits = [
      { split_index: 0, split_type: "climb_active_superseded", duration_s: 100 },
      { split_index: 1, split_type: "climb_active", duration_s: 20, climb_grade: 2, climb_result: "completed" },
      { split_index: 2, split_type: "climb_active", duration_s: null, climb_grade: null, climb_name: "Pink - A8", climb_result: "attempt" },
    ] as unknown as SplitOut[];
    const routes = boulderingRoutes(splits);
    expect(routes).toHaveLength(2);
    const summary = climbSummary(routes, splits);
    expect(summary.climbTimeS).toBe(120);
    expect(summary.routeCount).toBe(2);
    expect(summary.maxCompletedGrade).toBe(2);
  });
});
