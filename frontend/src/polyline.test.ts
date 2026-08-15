import { describe, expect, it } from "vitest";

import { decodePolyline } from "./polyline";

describe("decodePolyline", () => {
  it("decodes Google's own reference example correctly", () => {
    // From Google's Encoded Polyline Algorithm Format documentation -- the canonical worked
    // example every implementation of this algorithm is checked against.
    const points = decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@");
    expect(points).toHaveLength(3);
    expect(points[0]![0]).toBeCloseTo(38.5, 5);
    expect(points[0]![1]).toBeCloseTo(-120.2, 5);
    expect(points[1]![0]).toBeCloseTo(40.7, 5);
    expect(points[1]![1]).toBeCloseTo(-120.95, 5);
    expect(points[2]![0]).toBeCloseTo(43.252, 5);
    expect(points[2]![1]).toBeCloseTo(-126.453, 5);
  });

  it("returns an empty array for an empty string", () => {
    expect(decodePolyline("")).toEqual([]);
  });

  it("round-trips a single point back to itself", () => {
    // "??" encodes (0, 0) -- both coordinate deltas are zero, each represented by one "?" byte.
    expect(decodePolyline("??")).toEqual([[0, 0]]);
  });
});
