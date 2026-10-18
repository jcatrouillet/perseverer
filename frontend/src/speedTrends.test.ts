import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "./api/types";
import { speedTrendLine, speedTrendPoints } from "./speedTrends";

function ride(id: string, date: string, distanceM: number, seconds: number): ActivitySummary {
  return {
    id,
    local_date: date,
    sport: "cycling",
    name: null,
    distance_m: distanceM,
    moving_duration_s: seconds,
    duration_s: seconds,
  } as ActivitySummary;
}

describe("speedTrendPoints", () => {
  it("gives each ride its average moving speed, oldest first", () => {
    const points = speedTrendPoints([
      ride("b", "2026-02-01", 30000, 3600),
      ride("a", "2026-01-01", 20000, 3600),
    ]);
    expect(points.map((p) => p.id)).toEqual(["a", "b"]);
    expect(points[0]!.speedKmh).toBeCloseTo(20);
    expect(points[1]!.speedKmh).toBeCloseTo(30);
  });

  it("leaves out rides too short or too fast or slow to be real riding", () => {
    const points = speedTrendPoints([
      ride("short", "2026-01-01", 500, 120),
      ride("car", "2026-01-02", 80000, 3600),
      ride("crawl", "2026-01-03", 3000, 3600),
      ride("ok", "2026-01-04", 25000, 3600),
    ]);
    expect(points.map((p) => p.id)).toEqual(["ok"]);
  });
});

describe("speedTrendLine", () => {
  it("fits a straight line from the first ride to the last", () => {
    const line = speedTrendLine(
      speedTrendPoints([
        ride("a", "2026-01-01", 20000, 3600),
        ride("b", "2026-01-11", 25000, 3600),
        ride("c", "2026-01-21", 30000, 3600),
      ]),
    );
    expect(line?.start.speedKmh).toBeCloseTo(20);
    expect(line?.end.speedKmh).toBeCloseTo(30);
  });

  it("is null with a single ride", () => {
    expect(speedTrendLine(speedTrendPoints([ride("a", "2026-01-01", 20000, 3600)]))).toBeNull();
  });
});
