// Pure helpers for the Health page DEXA scans: look a measurement up by (region, metric), and
// derive the figures the report shows that are not stored (lean share, left/right gaps).
import type { DexaScanOut } from "./api/types";

export const BODY_REGIONS: { key: string; label: string }[] = [
  { key: "arms", label: "Arms" },
  { key: "legs", label: "Legs" },
  { key: "trunk", label: "Trunk" },
  { key: "android", label: "Android" },
  { key: "gynoid", label: "Gynoid" },
  { key: "total", label: "Total" },
];

export const LIMB_SIDES: { key: string; label: string }[] = [
  { key: "right_arm", label: "Right arm" },
  { key: "left_arm", label: "Left arm" },
  { key: "right_leg", label: "Right leg" },
  { key: "left_leg", label: "Left leg" },
];

export const BONE_REGIONS: { key: string; label: string }[] = [
  { key: "head", label: "Head" },
  { key: "arms", label: "Arms" },
  { key: "legs", label: "Legs" },
  { key: "trunk", label: "Trunk" },
  { key: "ribs", label: "Ribs" },
  { key: "spine", label: "Spine" },
  { key: "pelvis", label: "Pelvis" },
  { key: "total", label: "Total" },
];

/** The stored value for a region and metric, or null when the scan does not have it. */
export function dexaValue(scan: DexaScanOut, region: string, metric: string): number | null {
  const found = scan.values.find((v) => v.region === region && v.metric === metric);
  return found ? found.value_num : null;
}

/** Lean tissue as a share of the region total mass, in percent (null when either is missing). */
export function leanPct(scan: DexaScanOut, region: string): number | null {
  const lean = dexaValue(scan, region, "lean_mass_kg");
  const total = dexaValue(scan, region, "total_mass_kg");
  return lean !== null && total ? (lean / total) * 100 : null;
}

/** Right minus left lean mass of a limb pair, in kg (positive: the right side is heavier). */
export function leanGapKg(scan: DexaScanOut, limb: "arm" | "leg"): number | null {
  const right = dexaValue(scan, `right_${limb}`, "lean_mass_kg");
  const left = dexaValue(scan, `left_${limb}`, "lean_mass_kg");
  return right !== null && left !== null ? right - left : null;
}

export function formatKg(v: number | null, digits = 1): string {
  return v === null ? "-" : `${v.toFixed(digits)} kg`;
}

export function formatPct(v: number | null): string {
  return v === null ? "-" : `${v.toFixed(1)}%`;
}

export function formatNumber(v: number | null, digits: number): string {
  return v === null ? "-" : v.toFixed(digits);
}
