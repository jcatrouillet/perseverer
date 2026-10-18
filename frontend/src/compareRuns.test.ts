import { describe, expect, it } from "vitest";

import type { StreamResponse } from "./api/types";
import {
  addToSlots,
  alignRuns,
  compareColor,
  compareHref,
  fastestSimilarRun,
  parseCompareSport,
  MAX_COMPARED,
  parseSlots,
  prepareRunSeries,
  removeFromSlots,
  similarDistanceBand,
  similarRideBand,
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
    expect(series.channels.speed[5]).toBeNull();
  });

  it("reads a ride as speed in km/h, with cadence left in rpm and no pace or grade-adjusted pace", () => {
    const points = Array.from({ length: 30 }, (_, i) => ({ t: i, d: i * 8, v: 8, cad: 85 }));
    const series = prepareRunSeries(stream(points), "cycling")!;
    expect(series.channels.speed[5]).toBeCloseTo(28.8, 5);
    expect(series.channels.cadence[5]).toBe(85);
    expect(series.channels.pace.every((v) => v == null)).toBe(true);
    expect(series.channels.gap.every((v) => v == null)).toBe(true);
  });
});

describe("compare sport helpers", () => {
  it("falls back to running for anything but cycling", () => {
    expect(parseCompareSport("cycling")).toBe("cycling");
    expect(parseCompareSport("running")).toBe("running");
    expect(parseCompareSport("swimming")).toBe("running");
    expect(parseCompareSport(null)).toBe("running");
  });

  it("builds the page address with the sport only when it is not running", () => {
    expect(compareHref("running")).toBe("/activities/compare");
    expect(compareHref("running", "a,b")).toBe("/activities/compare?ids=a,b");
    expect(compareHref("cycling")).toBe("/activities/compare?sport=cycling");
    expect(compareHref("cycling", "a")).toBe("/activities/compare?sport=cycling&ids=a");
  });

  it("looks for rides within 10% of the selected ride's length", () => {
    const band = similarRideBand(64_000, "metric")!;
    expect(band.whole).toBe(64);
    expect(band.lowM).toBeCloseTo(57_600, 5);
    expect(band.highM).toBeCloseTo(70_400, 5);
    expect(similarRideBand(300, "metric")).toBeNull();
  });

  it("suggests the fastest similar ride by average speed, not by pace", () => {
    const ride = (id: string, distance_m: number, seconds: number) => ({
      id,
      distance_m,
      moving_duration_s: seconds,
      duration_s: seconds,
    });
    const selected = ride("me", 60_000, 7200); // 30 km/h
    const band = { lowM: 54_000, highM: 66_000 };
    const result = fastestSimilarRun(
      selected,
      [
        selected,
        ride("slow", 60_000, 9000),
        ride("quick", 60_000, 6300),
        ride("far", 90_000, 3600),
      ],
      band,
      "cycling",
    );
    expect(result?.run.id).toBe("quick");
    expect(result?.selectedIsFastest).toBe(false);
    const alone = fastestSimilarRun(
      selected,
      [selected, ride("slow", 60_000, 9000)],
      band,
      "cycling",
    );
    expect(alone?.selectedIsFastest).toBe(true);
  });
});

function runSeries(xsKm: number[], hr: number[]): RunSeries {
  const empty = xsKm.map(() => null);
  return {
    distanceKm: xsKm,
    timeS: xsKm.map((x) => x * 300),
    channels: {
      pace: empty,
      speed: empty,
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

function run(id: string, km: number, minutes: number) {
  return { id, distance_m: km * 1000, moving_duration_s: minutes * 60, duration_s: minutes * 60 };
}

describe("similar-distance suggestion", () => {
  it("uses the same whole kilometre, rounded down, or the same whole mile", () => {
    expect(similarDistanceBand(32_020, "metric")).toEqual({
      whole: 32,
      lowM: 32_000,
      highM: 33_000,
    });
    expect(similarDistanceBand(9_999.99999, "metric")?.whole).toBe(10);
    const miles = similarDistanceBand(16_100, "imperial")!; // 10.004 mi
    expect(miles.whole).toBe(10);
    expect(miles.lowM).toBeCloseTo(16_093.44, 2);
    expect(similarDistanceBand(800, "metric")).toBeNull();
  });

  it("suggests the fastest other run in the band, or the next fastest when this one is fastest", () => {
    const selected = run("me", 32.02, 200); // 6:15 /km
    const band = similarDistanceBand(32_020, "metric")!;
    const candidates = [
      selected,
      run("slow", 32.5, 210),
      run("fast", 32.1, 185), // 5:46 /km
      run("faster-but-33", 33.0, 170), // outside: 33 km is the next band
      run("short", 31.9, 150), // outside: 31 km band
    ];
    expect(fastestSimilarRun(selected, candidates, band)).toEqual({
      run: candidates[2],
      selectedIsFastest: false,
    });

    const quick = run("me", 32.02, 180);
    expect(fastestSimilarRun(quick, candidates, band)).toEqual({
      run: candidates[2],
      selectedIsFastest: true,
    });
    expect(fastestSimilarRun(selected, [selected], band)).toBeNull();
  });
});
