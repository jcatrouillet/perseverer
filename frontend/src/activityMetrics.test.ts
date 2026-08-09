import { describe, expect, it } from "vitest";

import type { ActivityMetricOut } from "./api/types";
import { extractHrZones, hrZoneRangeLabel, metricValue } from "./activityMetrics";

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
