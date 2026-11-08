import { describe, expect, it } from "vitest";

import type { DexaScanOut } from "./api/types";
import { dexaValue, formatKg, leanGapKg, leanPct } from "./dexaScans";

const scan: DexaScanOut = {
  local_date: "2025-11-23",
  provider: "BodySpec",
  values: [
    { region: "total", metric: "lean_mass_kg", value_num: 61 },
    { region: "total", metric: "total_mass_kg", value_num: 84 },
    { region: "right_leg", metric: "lean_mass_kg", value_num: 11 },
    { region: "left_leg", metric: "lean_mass_kg", value_num: 10.5 },
  ],
};

describe("dexaScans", () => {
  it("looks a value up by region and metric, null when absent", () => {
    expect(dexaValue(scan, "total", "lean_mass_kg")).toBe(61);
    expect(dexaValue(scan, "arms", "lean_mass_kg")).toBeNull();
  });

  it("derives the lean share and the right-left gap", () => {
    expect(leanPct(scan, "total")).toBeCloseTo(72.6, 1);
    expect(leanPct(scan, "arms")).toBeNull();
    expect(leanGapKg(scan, "leg")).toBeCloseTo(0.5);
    expect(leanGapKg(scan, "arm")).toBeNull();
  });

  it("never prints a missing value as zero", () => {
    expect(formatKg(null)).toBe("-");
    expect(formatKg(61.04)).toBe("61.0 kg");
  });
});
