import { describe, expect, it } from "vitest";
import type { ActivitySummary } from "./api/types";
import { prProgress, yearAgo } from "./prProgress";

function run(
  id: string,
  date: string,
  distance: number,
  pace: number,
  vdot = 45,
  overrides: Partial<ActivitySummary> = {},
): ActivitySummary {
  return {
    id,
    local_date: date,
    sport: "running",
    distance_m: distance,
    moving_duration_s: (pace * distance) / 1000,
    vdot,
    ...overrides,
  } as ActivitySummary;
}

describe("PR progress", () => {
  it("selects one best VDOT per Monday–Sunday week overall, without rounding VDOT", () => {
    const result = prProgress(
      [
        run("lower", "2026-09-14", 5000, 270, 45.11),
        run("winner", "2026-09-20", 10000, 290, 45.12),
        run("previous", "2026-09-13", 6000, 280, 44),
      ],
      "2026-09-20",
    );
    expect(result.recent.map((p) => p.activity.id)).toEqual(["previous", "winner"]);
  });

  it("puts the one-year anniversary in older, including leap-day clamping", () => {
    expect(yearAgo("2024-02-29")).toBe("2023-02-28");
    const result = prProgress(
      [run("old", "2025-09-20", 5000, 290), run("new", "2025-09-22", 5100, 280)],
      "2026-09-20",
    );
    expect(result.older.map((p) => p.activity.id)).toEqual(["old"]);
    expect(result.recent.map((p) => p.activity.id)).toEqual(["new"]);
  });

  it("uses exact distances and removes slower runs dominated by longer runs from the red line only", () => {
    const result = prProgress(
      [
        run("short", "2024-01-01", 5001, 250),
        run("close", "2024-01-08", 5013, 260),
        run("dominated", "2024-01-15", 8000, 320),
        run("long", "2024-01-22", 10000, 300),
      ],
      "2026-09-20",
    );
    expect(result.baseline.map((p) => p.distanceKm)).toEqual([5.001, 5.013, 10]);
    expect(result.older).toHaveLength(4);
  });

  it("draws blue only where recent records improve the older stepped frontier", () => {
    const result = prProgress(
      [
        run("old5", "2024-01-01", 5000, 250),
        run("old10", "2024-01-08", 10000, 300),
        run("new8", "2026-01-05", 8123, 280),
        run("newSlow", "2026-01-12", 9000, 310),
      ],
      "2026-09-20",
    );
    expect(result.improvements).toEqual([
      [
        { distanceKm: 5, pace: expect.closeTo(280, 8) },
        { distanceKm: 8.123, pace: expect.closeTo(280, 8) },
      ],
    ]);
    expect(result.recent).toHaveLength(2);
  });

  it("does not label ties or missing older coverage as improvements", () => {
    expect(prProgress([run("new", "2026-01-05", 10000, 250)], "2026-09-20").improvements).toEqual(
      [],
    );
    const result = prProgress(
      [run("old", "2024-01-01", 10000, 250), run("tie", "2026-01-05", 10000, 250)],
      "2026-09-20",
    );
    expect(result.improvements).toEqual([]);
    expect(result.combined[0].activity.id).toBe("old");
  });

  it("keeps disconnected improvements separate across a non-improving interval", () => {
    const result = prProgress(
      [
        run("old5", "2024-01-01", 5000, 250),
        run("old10", "2024-01-08", 10000, 300),
        run("new3", "2026-01-05", 3000, 240),
        run("new8", "2026-01-12", 8000, 280),
      ],
      "2026-09-20",
    );
    expect(result.improvements).toEqual([
      [
        { distanceKm: 0, pace: 240 },
        { distanceKm: 3, pace: 240 },
      ],
      [
        { distanceKm: 5, pace: 280 },
        { distanceKm: 8, pace: 280 },
      ],
    ]);
  });

  it("filters unusable, non-running and future records before choosing weekly winners", () => {
    const result = prProgress(
      [
        run("valid", "2026-01-05", 5000, 280),
        run("missing", "2026-01-06", 0, 280, 99),
        run("nan", "2026-01-07", 5000, 280, NaN),
        run("bike", "2026-01-08", 5000, 200, 99, { sport: "cycling" }),
        run("future", "2027-01-01", 5000, 280),
        run("noduration", "2026-02-01", 5000, 280, 60, {
          moving_duration_s: null,
          duration_s: null,
        }),
      ],
      "2026-09-20",
    );
    expect(result.recent.map((p) => p.activity.id)).toEqual(["valid"]);
  });

  it("falls back to elapsed duration and selects stable weekly ties independent of input order", () => {
    const a = run("a", "2026-01-05", 5000, 280, 45, { moving_duration_s: null, duration_s: 1400 });
    const b = run("b", "2026-01-06", 10000, 300);
    expect(prProgress([b, a], "2026-09-20").recent[0].activity.id).toBe("a");
    expect(prProgress([a, b], "2026-09-20").recent[0].pace).toBe(280);
  });
});
