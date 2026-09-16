import { describe, expect, it } from "vitest";

import {
  displayDistanceToMeters,
  distanceUnitLabel,
  formatDistanceValue,
  formatPaceValue,
  kmhToDisplaySpeed,
  metersToDisplayDistance,
  paceMinPerDisplayUnit,
  speedUnitLabel,
} from "./formatDistance";

describe("metersToDisplayDistance", () => {
  it("converts meters to km for metric", () => {
    expect(metersToDisplayDistance(5000, "metric")).toBe(5);
  });

  it("converts meters to miles for imperial", () => {
    expect(metersToDisplayDistance(1609.344, "imperial")).toBeCloseTo(1, 6);
  });
});

describe("displayDistanceToMeters", () => {
  it("is the exact inverse of metersToDisplayDistance for metric", () => {
    expect(displayDistanceToMeters(5, "metric")).toBe(5000);
  });

  it("is the exact inverse of metersToDisplayDistance for imperial", () => {
    expect(displayDistanceToMeters(1, "imperial")).toBeCloseTo(1609.344, 6);
  });
});

describe("distanceUnitLabel", () => {
  it("returns km for metric", () => {
    expect(distanceUnitLabel("metric")).toBe("km");
  });

  it("returns mi for imperial", () => {
    expect(distanceUnitLabel("imperial")).toBe("mi");
  });
});

describe("formatDistanceValue", () => {
  it("formats a metric distance with the km suffix", () => {
    expect(formatDistanceValue(5000, "metric")).toBe("5.0 km");
  });

  it("formats an imperial distance with the mi suffix", () => {
    // 8046.72m = 5.0 miles exactly.
    expect(formatDistanceValue(8046.72, "imperial")).toBe("5.0 mi");
  });

  it("respects a custom decimals count", () => {
    expect(formatDistanceValue(5000, "metric", 2)).toBe("5.00 km");
  });
});

describe("formatPaceValue", () => {
  it("formats a metric pace as M:SS /km unchanged", () => {
    expect(formatPaceValue(300, "metric")).toBe("5:00 /km");
  });

  it("converts a per-km pace to per-mile for imperial (slower number, since a mile is longer)", () => {
    // 300 s/km * 1.609344 = 482.8032 s/mi -> 8:03 /mi (rounded).
    expect(formatPaceValue(300, "imperial")).toBe("8:03 /mi");
  });
});

describe("paceMinPerDisplayUnit", () => {
  it("returns bare minutes/km unchanged for metric", () => {
    expect(paceMinPerDisplayUnit(300, "metric")).toBe(5);
  });

  it("returns bare minutes/mile for imperial", () => {
    expect(paceMinPerDisplayUnit(300, "imperial")).toBeCloseTo(8.04672, 5);
  });
});

describe("kmhToDisplaySpeed", () => {
  it("returns km/h unchanged for metric", () => {
    expect(kmhToDisplaySpeed(30, "metric")).toBe(30);
  });

  it("converts km/h to mph for imperial", () => {
    expect(kmhToDisplaySpeed(1.609344, "imperial")).toBeCloseTo(1, 6);
  });
});

describe("speedUnitLabel", () => {
  it("returns km/h for metric", () => {
    expect(speedUnitLabel("metric")).toBe("km/h");
  });

  it("returns mph for imperial", () => {
    expect(speedUnitLabel("imperial")).toBe("mph");
  });
});
