import { describe, expect, it } from "vitest";

import type { StreamResponse } from "./api/types";
import {
  addToSlots,
  alignRuns,
  compareColor,
  MAX_COMPARED,
  parseSlots,
  prepareRunSeries,
  removeFromSlots,
  slotsToParam,
  type RunSeries,
} from "./compareRuns";

describe("colour slots", () => {
  it("keeps each run's slot when another is removed, and reuses the first free slot", () => {
    let slots = parseSlots("a,b,c");
    slots = removeFromSlots(slots, "b");
    expect(slots).toEqual(["a", null, "c"]);
    expect(slotsToParam(slots)).toBe("a,,c");
    slots = addToSlots(slots, "d");
    expect(slots).toEqual(["a", "d", "c"]);
    expect(compareColor(1)).toBe("var(--color-compare-2)");
  });

  it("caps the comparison at eight runs and ignores duplicates", () => {
    let slots = parseSlots("a,a,b");
    expect(slots).toEqual(["a", null, "b"]);
    for (const id of ["c", "d", "e", "f", "g", "h", "i", "j"]) slots = addToSlots(slots, id);
    expect(slots).toHaveLength(MAX_COMPARED);
    expect(slots).not.toContain("i");
    expect(addToSlots(slots, "a")).toBe(slots);
  });

  it("drops trailing free slots and reads an empty parameter as no runs", () => {
    expect(removeFromSlots(["a", "b"], "b")).toEqual(["a"]);
    expect(parseSlots(null)).toEqual([]);
    expect(parseSlots(",,")).toEqual([]);
  });
});

function stream(points: { t: number; d: number; v: number; cad: number }[]): StreamResponse {
  const start = Date.UTC(2026, 0, 1, 8);
  return {
    activity_id: "x",
    tier: "medium",
    channels: ["distance_m", "speed_mps", "cadence", "heart_rate"],
    timestamps: points.map((p) => new Date(start + p.t * 1000).toISOString()),
    series: {
      distance_m: points.map((p) => p.d),
      speed_mps: points.map((p) => p.v),
      cadence: points.map((p) => p.cad),
      heart_rate: points.map(() => 150),
    },
  };
}

describe("prepareRunSeries", () => {
  it("doubles the per-foot cadence and converts speed to pace", () => {
    const points = Array.from({ length: 30 }, (_, i) => ({ t: i, d: i * 3, v: 3, cad: 85 }));
    const series = prepareRunSeries(stream(points))!;
    expect(series.channels.cadence[5]).toBe(170);
    expect(series.channels.pace[5]).toBeCloseTo(1000 / 3 / 60, 5); // 5:33 /km
    expect(series.channels.pace[0]).toBeNull(); // standing start
    expect(series.distanceKm[10]).toBeCloseTo(0.03, 5);
  });
});

function runSeries(xsKm: number[], hr: number[]): RunSeries {
  const empty = xsKm.map(() => null);
  return {
    distanceKm: xsKm,
    timeS: xsKm.map((x) => x * 300),
    channels: {
      pace: empty,
      gap: empty,
      heart_rate: hr,
      cadence: empty,
      altitude: empty,
      power: empty,
    },
  };
}

describe("alignRuns", () => {
  it("averages every run into shared buckets, with gaps past a shorter run's end", () => {
    const long = runSeries([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], new Array(11).fill(150));
    const short = runSeries([0, 1, 2, 3, 4], [140, 140, 160, 160, 160]);
    const rows = alignRuns(
      [
        { slot: 0, series: long },
        { slot: 3, series: short },
      ],
      "heart_rate",
      "distance",
      5,
    );
    expect(rows.map((r) => r.x)).toEqual([0, 2, 4, 6, 8]);
    expect(rows[0]).toMatchObject({ s0: 150, s3: 140 });
    expect(rows[1]).toMatchObject({ s3: 160 });
    expect(rows[3]!.s3).toBeNull();
    expect(rows[4]!.s0).toBe(150); // the final km falls in the last bucket
  });

  it("returns nothing when no run has an axis", () => {
    expect(alignRuns([], "pace", "time")).toEqual([]);
  });
});
