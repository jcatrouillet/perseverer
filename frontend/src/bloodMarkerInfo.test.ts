import { describe, expect, it } from "vitest";

import { markerDescription } from "./bloodMarkerInfo";

describe("markerDescription", () => {
  it("looks a marker up ignoring case and surrounding spaces", () => {
    expect(markerDescription("  HbA1c ")).toMatch(/three months/);
    expect(markerDescription("LDL Cholesterol (calc)")).toMatch(/calculated/i);
  });

  it("returns null for a marker with no description", () => {
    expect(markerDescription("Some Brand New Marker")).toBeNull();
  });

  it("never states a reference range or target (this app only shows the lab's own)", () => {
    const names = [
      "Total Cholesterol",
      "Glucose",
      "Potassium",
      "TSH",
      "Hemoglobin",
      "ALT",
      "Lactate",
      "eGFR",
    ];
    for (const n of names) {
      expect(markerDescription(n)).not.toMatch(/\b(normal|healthy|should be|ideal|optimal)\b/i);
    }
  });
});
