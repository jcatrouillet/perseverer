import { describe, expect, it } from "vitest";

import type { BloodTestResultOut } from "./api/types";
import { groupResultsByMarker, isOutOfRange, markerCategory } from "./bloodMarkers";

function result(overrides: Partial<BloodTestResultOut> = {}): BloodTestResultOut {
  return {
    id: 1,
    local_date: "2026-06-01",
    marker: "LDL Cholesterol (calc)",
    value_num: 110,
    unit: "mg/dL",
    reference_low: null,
    reference_high: 130,
    lab_name: null,
    notes: null,
    created_at: "2026-06-01T00:00:00",
    updated_at: "2026-06-01T00:00:00",
    ...overrides,
  };
}

describe("isOutOfRange", () => {
  it("judges a value against the range stored on its own result", () => {
    expect(isOutOfRange(result({ value_num: 139, reference_high: 130 }))).toBe(true);
    expect(isOutOfRange(result({ value_num: 82, reference_high: 130 }))).toBe(false);
    expect(isOutOfRange(result({ value_num: 44, reference_low: 49, reference_high: 74 }))).toBe(true);
  });

  it("never flags a result with no range at all", () => {
    expect(isOutOfRange(result({ reference_low: null, reference_high: null, value_num: 9999 }))).toBe(
      false,
    );
  });

  it("treats a value exactly on a bound as in range", () => {
    expect(isOutOfRange(result({ value_num: 130, reference_high: 130 }))).toBe(false);
    expect(isOutOfRange(result({ value_num: 40, reference_low: 40, reference_high: null }))).toBe(false);
  });
});

describe("markerCategory", () => {
  it.each([
    ["Total Cholesterol", "Lipids"],
    ["HDL Cholesterol", "Lipids"],
    ["Cholesterol/HDL Ratio", "Lipids"],
    ["Triglycerides", "Lipids"],
    ["HbA1c", "Glucose & thyroid"],
    ["Glucose", "Glucose & thyroid"],
    ["Estimated Average Glucose", "Glucose & thyroid"],
    ["TSH", "Glucose & thyroid"],
    ["Sodium", "Electrolytes & kidney"],
    ["eGFR", "Electrolytes & kidney"],
    ["CO2 (Bicarbonate)", "Electrolytes & kidney"],
    ["ALT", "Liver & pancreas"],
    ["AST", "Liver & pancreas"],
    ["Alkaline Phosphatase", "Liver & pancreas"],
    ["Total Protein", "Liver & pancreas"],
    ["Hemoglobin", "Blood count"],
    ["MCHC", "Blood count"],
    ["Neutrophils (abs)", "Blood count"],
    ["Immature Granulocytes %", "Blood count"],
    ["Venous pH", "Blood gas"],
    ["Lactate", "Blood gas"],
    ["Vitamin D", "Other"],
  ])("puts %s under %s", (marker, category) => {
    expect(markerCategory(marker)).toBe(category);
  });
});

describe("groupResultsByMarker", () => {
  it("makes one series per marker, oldest result first, ordered by category then name", () => {
    const series = groupResultsByMarker([
      result({ id: 1, marker: "Sodium", local_date: "2026-01-01" }),
      result({ id: 2, marker: "Total Cholesterol", local_date: "2025-05-01" }),
      result({ id: 3, marker: "Total Cholesterol", local_date: "2020-05-01" }),
      result({ id: 4, marker: "HDL Cholesterol", local_date: "2020-05-01" }),
    ]);
    expect(series.map((s) => s.marker)).toEqual([
      "HDL Cholesterol",
      "Total Cholesterol",
      "Sodium",
    ]);
    expect(series[1]!.results.map((r) => r.local_date)).toEqual(["2020-05-01", "2025-05-01"]);
  });
});
