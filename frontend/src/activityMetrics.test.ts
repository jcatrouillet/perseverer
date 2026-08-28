import { describe, expect, it } from "vitest";

import type { ActivityMetricOut } from "./api/types";
import { computeHrZonesFromStream, extractHrZones, hrZoneRangeLabel, metricValue } from "./activityMetrics";

function metric(metric_key: string, value_num: number | null): ActivityMetricOut {
  return { metric_key, value_num, value_text: null, unit: null, source: "test" };
}

describe("metricValue", () => {
  it("finds a metric by key", () => {
    const metrics = [metric("fit.session.avg_temperature", 27.0)];
    expect(metricValue(metrics, "fit.session.avg_temperature")).toBe(27.0);
  });

  it("returns null when the key isn't present", () => {
    expect(metricValue([], "fit.session.avg_power")).toBeNull();
  });
});

describe("extractHrZones", () => {
  // The exact shape confirmed against a real device (see fit/parser.py's
  // _time_in_zone_metrics docstring): 7 time buckets for 6 configured boundaries.
  const realShapeMetrics: ActivityMetricOut[] = [
    metric("fit.time_in_zone.time_in_hr_zone_0", 729.172),
    metric("fit.time_in_zone.time_in_hr_zone_1", 1544.902),
    metric("fit.time_in_zone.time_in_hr_zone_2", 77.001),
    metric("fit.time_in_zone.time_in_hr_zone_3", 0.0),
    metric("fit.time_in_zone.time_in_hr_zone_4", 0.0),
    metric("fit.time_in_zone.time_in_hr_zone_5", 0.0),
    metric("fit.time_in_zone.time_in_hr_zone_6", 0.0),
    metric("fit.time_in_zone.hr_zone_high_boundary_0", 87),
    metric("fit.time_in_zone.hr_zone_high_boundary_1", 106),
    metric("fit.time_in_zone.hr_zone_high_boundary_2", 126),
    metric("fit.time_in_zone.hr_zone_high_boundary_3", 140),
    metric("fit.time_in_zone.hr_zone_high_boundary_4", 161),
    metric("fit.time_in_zone.hr_zone_high_boundary_5", 177),
  ];

  it("reassembles 7 zones (6 boundaries) in order", () => {
    const zones = extractHrZones(realShapeMetrics);
    expect(zones).not.toBeNull();
    expect(zones).toHaveLength(7);
    expect(zones!.map((z) => z.index)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(zones![1]).toEqual({ index: 1, seconds: 1544.902, lowBoundary: 87, highBoundary: 106 });
  });

  it("gives zone 0 an open bottom and the last zone an open top", () => {
    const zones = extractHrZones(realShapeMetrics)!;
    expect(zones[0]).toMatchObject({ lowBoundary: null, highBoundary: 87 });
    expect(zones[6]).toMatchObject({ lowBoundary: 177, highBoundary: null });
  });

  it("adapts to a different zone count rather than assuming 5 or 6", () => {
    const threeZones: ActivityMetricOut[] = [
      metric("fit.time_in_zone.time_in_hr_zone_0", 10),
      metric("fit.time_in_zone.time_in_hr_zone_1", 20),
      metric("fit.time_in_zone.hr_zone_high_boundary_0", 150),
    ];
    const zones = extractHrZones(threeZones);
    expect(zones).toHaveLength(2);
    expect(zones![1]).toMatchObject({ lowBoundary: 150, highBoundary: null });
  });

  it("returns null (not an empty array) when the activity has no time-in-zone data", () => {
    expect(extractHrZones([metric("fit.session.avg_temperature", 20)])).toBeNull();
  });
});

describe("hrZoneRangeLabel", () => {
  it("formats an open-bottom zone", () => {
    expect(hrZoneRangeLabel({ index: 0, seconds: 1, lowBoundary: null, highBoundary: 87 })).toBe(
      "< 87",
    );
  });

  it("formats a bounded zone", () => {
    expect(
      hrZoneRangeLabel({ index: 1, seconds: 1, lowBoundary: 87, highBoundary: 106 }),
    ).toBe("87 – 106");
  });

  it("formats an open-top zone", () => {
    expect(hrZoneRangeLabel({ index: 6, seconds: 1, lowBoundary: 177, highBoundary: null })).toBe(
      "177+",
    );
  });
});

describe("computeHrZonesFromStream", () => {
  // Boundaries: Z1 <120, Z2 120-140, Z3 140-155, Z4 155-170, Z5 170+.
  const boundaries: [number, number, number, number] = [120, 140, 155, 170];

  function ts(startIso: string, offsetsS: number[]): string[] {
    const start = new Date(startIso).getTime();
    return offsetsS.map((s) => new Date(start + s * 1000).toISOString());
  }

  it("attributes each sample's own interval (up to the next sample) to its own zone", () => {
    // 0-10s @110 (Z1), 10-20s @130 (Z2), 20-30s @160 (Z4), last sample contributes nothing.
    const hr = [110, 130, 160, 160];
    const timestamps = ts("2026-01-01T00:00:00Z", [0, 10, 20, 30]);
    const zones = computeHrZonesFromStream(hr, timestamps, boundaries);
    expect(zones).not.toBeNull();
    expect(zones!.find((z) => z.index === 1)!.seconds).toBe(10);
    expect(zones!.find((z) => z.index === 2)!.seconds).toBe(10);
    expect(zones!.find((z) => z.index === 4)!.seconds).toBe(10);
  });

  it("always returns all 5 zones, zero-filled for ones never reached", () => {
    const hr = [110, 110];
    const timestamps = ts("2026-01-01T00:00:00Z", [0, 10]);
    const zones = computeHrZonesFromStream(hr, timestamps, boundaries);
    expect(zones).toHaveLength(5);
    expect(zones!.map((z) => z.index)).toEqual([1, 2, 3, 4, 5]);
    expect(zones!.find((z) => z.index === 3)!.seconds).toBe(0);
  });

  it("gives zone 1 an open bottom and zone 5 an open top, matching the configured boundaries", () => {
    const hr = [110, 175, 175];
    const timestamps = ts("2026-01-01T00:00:00Z", [0, 10, 20]);
    const zones = computeHrZonesFromStream(hr, timestamps, boundaries)!;
    expect(zones[0]).toMatchObject({ lowBoundary: null, highBoundary: 120 });
    expect(zones[4]).toMatchObject({ lowBoundary: 170, highBoundary: null });
  });

  it("skips a null HR sample's own interval without crashing", () => {
    const hr = [110, null, 130];
    const timestamps = ts("2026-01-01T00:00:00Z", [0, 10, 20]);
    const zones = computeHrZonesFromStream(hr, timestamps, boundaries);
    expect(zones).not.toBeNull();
    expect(zones!.find((z) => z.index === 1)!.seconds).toBe(10);
    const total = zones!.reduce((sum, z) => sum + z.seconds, 0);
    expect(total).toBe(10); // only the first interval counted -- the null sample's is skipped
  });

  it("returns null when the stream has no usable HR data at all", () => {
    expect(computeHrZonesFromStream([null, null], ts("2026-01-01T00:00:00Z", [0, 10]), boundaries)).toBeNull();
  });

  it("returns null for empty or mismatched-length arrays", () => {
    expect(computeHrZonesFromStream([], [], boundaries)).toBeNull();
    expect(computeHrZonesFromStream([110], [], boundaries)).toBeNull();
  });

  describe("with a speed stream (moving-time exclusion)", () => {
    // Regression test for a confirmed real bug: without a speed check, a device pause (elapsed
    // time keeps ticking, HR often coasts down slowly rather than dropping instantly) attributed
    // several real minutes to whatever zone the athlete happened to be in when they stopped --
    // reported live: ~7 stopped minutes almost all landing in Z2, pushing it from 48% to 55%.

    it("excludes a stopped interval (below the stationary floor) from every zone's total", () => {
      // 0-10s @130 moving (Z2), 10-20s @130 but stopped (speed 0), 20-30s @130 moving again.
      const hr = [130, 130, 130, 130];
      const timestamps = ts("2026-01-01T00:00:00Z", [0, 10, 20, 30]);
      const speedMps = [3.0, 0.0, 3.0, 3.0];
      const zones = computeHrZonesFromStream(hr, timestamps, boundaries, speedMps)!;
      expect(zones.find((z) => z.index === 2)!.seconds).toBe(20); // only the two moving intervals
      const total = zones.reduce((sum, z) => sum + z.seconds, 0);
      expect(total).toBe(20); // the stopped 10s interval contributes to no zone at all
    });

    it("treats a null speed sample the same as stopped, not as moving", () => {
      const hr = [130, 130];
      const timestamps = ts("2026-01-01T00:00:00Z", [0, 10]);
      const speedMps = [null, null];
      const zones = computeHrZonesFromStream(hr, timestamps, boundaries, speedMps);
      expect(zones).toBeNull(); // nothing usable at all
    });

    it("omitting speedMps counts every interval, unchanged from before", () => {
      const hr = [130, 130];
      const timestamps = ts("2026-01-01T00:00:00Z", [0, 10]);
      const zones = computeHrZonesFromStream(hr, timestamps, boundaries)!;
      expect(zones.find((z) => z.index === 2)!.seconds).toBe(10);
    });
  });

  describe("a genuine recording gap (no samples, not just a stopped one)", () => {
    // Regression test for a confirmed real bug: an 8-minute standing rest between reps had no
    // recorded samples at all (not even stationary ones) -- the whole 467s gap was attributed to
    // the one HR reading from *before* it, carrying a Z2 effort forward across a real rest.

    it("excludes an interval wider than the max sample gap, even without a speed stream", () => {
      // 0-10s @133 (Z2, normal 10s sample spacing) then a 467s gap to the next real sample @95
      // (Z1) -- the gap itself must contribute to neither zone.
      const hr = [133, 95, 95];
      const timestamps = ts("2026-01-01T00:00:00Z", [0, 10, 10 + 467]);
      const zones = computeHrZonesFromStream(hr, timestamps, boundaries)!;
      expect(zones.find((z) => z.index === 2)!.seconds).toBe(10); // only the real 10s interval
      expect(zones.find((z) => z.index === 1)!.seconds).toBe(0); // the gap itself: excluded
      const total = zones.reduce((sum, z) => sum + z.seconds, 0);
      expect(total).toBe(10);
    });

    it("still excludes a wide gap even when a speed stream says the pre-gap sample was moving", () => {
      // The speed check alone (moving-time exclusion) only ever looks at whether the sample
      // *before* a gap was itself moving -- it has no way to know what happened during a gap
      // with no samples in it at all. The gap-width check must catch this independently: with
      // only these two samples and the one interval between them excluded, nothing is left.
      const hr = [133, 95];
      const timestamps = ts("2026-01-01T00:00:00Z", [0, 467]);
      const speedMps = [3.0, 3.0]; // both "moving" by the speed check alone
      const zones = computeHrZonesFromStream(hr, timestamps, boundaries, speedMps);
      expect(zones).toBeNull();
    });

    it("keeps a normal ~1Hz interval, right at the boundary of the max gap", () => {
      const hr = [133, 133];
      const timestamps = ts("2026-01-01T00:00:00Z", [0, 10]); // exactly at the 10s cap
      const zones = computeHrZonesFromStream(hr, timestamps, boundaries)!;
      expect(zones.find((z) => z.index === 2)!.seconds).toBe(10);
    });
  });
});
