import { describe, expect, it } from "vitest";

import type { ActivitySummary, CyclingPowerPointOut } from "./api/types";
import { ftpLine, powerTrendPoints } from "./powerTrends";

function ride(id: string, date: string, best20: number | null): ActivitySummary {
  return {
    id,
    local_date: date,
    sport: "cycling",
    name: id,
    best_20min_power_w: best20,
  } as ActivitySummary;
}

function point(asOf: string, ftp: number | null, weight: number | null): CyclingPowerPointOut {
  return {
    as_of: asOf,
    ftp_w: ftp,
    ftp_w_per_kg: ftp != null && weight != null ? ftp / weight : null,
    cp_w: null,
    w_prime_j: null,
    vo2max: null,
    weight_kg: weight,
    best_powers: [],
  };
}

describe("powerTrendPoints", () => {
  it("plots each ride's best 20 minutes, with W/kg from the weight in force that day", () => {
    const history = [point("2025-01-10", 190, 80), point("2025-03-01", 200, 78)];
    const points = powerTrendPoints(
      [
        ride("later", "2025-03-05", 210),
        ride("early", "2025-01-10", 200),
        ride("no-power", "2025-02-01", null),
      ],
      history,
    );
    expect(points.map((p) => p.id)).toEqual(["early", "later"]);
    expect(points[0]!.wattsPerKg).toBeCloseTo(200 / 80);
    expect(points[1]!.wattsPerKg).toBeCloseTo(210 / 78); // the March weight
  });

  it("has no W/kg before any weight is known", () => {
    expect(powerTrendPoints([ride("r", "2025-01-01", 200)], [])[0]!.wattsPerKg).toBeNull();
  });
});

describe("ftpLine", () => {
  it("keeps the dates where FTP was known", () => {
    const line = ftpLine([point("2025-01-10", 190, 80), point("2025-02-10", null, 80)]);
    expect(line).toHaveLength(1);
    expect(line[0]!.ftpWPerKg).toBeCloseTo(190 / 80);
  });
});
