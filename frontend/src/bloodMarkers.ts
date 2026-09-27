// Pure helpers for treating blood test results as per-MARKER time series (Health page, left-hand
// list entry per marker) rather than per-draw-date panels. Reference ranges are still only ever the
// athlete's own, copied from each lab report -- every "out of range" judgement here is made against
// the range stored on that same result row, never a canonical range this app asserts (ranges
// differ between labs and change over the years, so a value is judged against the range printed
// on its own report).
import type { BloodTestResultOut } from "./api/types";
import { parseIsoDate } from "./dateUtils";

export function isOutOfRange(r: BloodTestResultOut): boolean {
  return (
    (r.reference_low != null && r.value_num < r.reference_low) ||
    (r.reference_high != null && r.value_num > r.reference_high)
  );
}

export function formatRange(r: BloodTestResultOut): string {
  if (r.reference_low == null && r.reference_high == null) return "—";
  if (r.reference_low != null && r.reference_high != null) {
    return `${r.reference_low}–${r.reference_high}`;
  }
  if (r.reference_low != null) return `≥ ${r.reference_low}`;
  return `≤ ${r.reference_high}`;
}

export function formatDate(localDate: string): string {
  // timeZone: "UTC" is required, not decorative -- parseIsoDate returns a UTC-midnight Date, and
  // toLocaleDateString defaults to the browser's own local timezone, which silently rolls the
  // displayed date back a day for anyone west of UTC (confirmed live: entering "2026-09-13"
  // rendered back as "Sep 12, 2026" without this). Same fix RunningStats.tsx's own
  // formatShortDate already applies to the identical parse-then-format shape.
  return parseIsoDate(localDate).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** Heading a marker is listed under in the Health page's left-hand list -- a navigation aid only
 * (there are dozens of markers once a CBC with differential is in the mix), never a clinical
 * classification. First matching rule wins; anything unrecognised lands in "Other". */
export const MARKER_CATEGORIES = [
  "Lipids",
  "Glucose & thyroid",
  "Electrolytes & kidney",
  "Liver & pancreas",
  "Blood count",
  "Blood gas",
  "Other",
] as const;
export type MarkerCategory = (typeof MARKER_CATEGORIES)[number];

const CATEGORY_RULES: [MarkerCategory, RegExp][] = [
  ["Blood gas", /\b(venous|arterial|base excess|lactate)\b/i],
  ["Lipids", /cholesterol|triglycerid|\b[hlv]dl\b/i],
  ["Glucose & thyroid", /glucose|hba1c|a1c|\btsh\b|\bt3\b|\bt4\b|thyroid/i],
  [
    "Electrolytes & kidney",
    /\b(sodium|potassium|chloride|co2|anion gap|bun|creatinine|egfr|calcium|magnesium|phosphorus|phosphate)\b/i,
  ],
  [
    "Liver & pancreas",
    /\b(alt|ast|alkaline phosphatase|bilirubin|albumin|total protein|globulin|lipase|ggt)\b/i,
  ],
  [
    "Blood count",
    /\b(wbc|rbc|hemoglobin|hematocrit|mcv|mch|mchc|rdw|platelets?|neutrophils?|lymphocytes?|monocytes?|eosinophils?|basophils?|immature granulocytes)\b/i,
  ],
];

export function markerCategory(marker: string): MarkerCategory {
  for (const [category, pattern] of CATEGORY_RULES) {
    if (pattern.test(marker)) return category;
  }
  return "Other";
}

export interface MarkerSeries {
  marker: string;
  category: MarkerCategory;
  /** Oldest first -- the order a time-series chart wants. */
  results: BloodTestResultOut[];
}

/** One series per distinct marker name, ordered by category then name. Results within a series
 * are sorted oldest-first regardless of the order the API returned them in. */
export function groupResultsByMarker(results: BloodTestResultOut[]): MarkerSeries[] {
  const byMarker = new Map<string, BloodTestResultOut[]>();
  for (const r of results) {
    const list = byMarker.get(r.marker) ?? [];
    list.push(r);
    byMarker.set(r.marker, list);
  }
  const series: MarkerSeries[] = [...byMarker.entries()].map(([marker, rows]) => ({
    marker,
    category: markerCategory(marker),
    results: [...rows].sort((a, b) =>
      a.local_date === b.local_date ? a.id - b.id : a.local_date < b.local_date ? -1 : 1,
    ),
  }));
  return series.sort(
    (a, b) =>
      MARKER_CATEGORIES.indexOf(a.category) - MARKER_CATEGORIES.indexOf(b.category) ||
      a.marker.localeCompare(b.marker),
  );
}
