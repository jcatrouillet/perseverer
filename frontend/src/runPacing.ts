// Text for a run's "pacing" insight (GET /activities/{id}/insights, kind "pacing" -- see
// insights/rules_activity.py::pacing_insight), in the athlete's own distance unit. The server's
// title is always per km; this rebuilds it from the paces in `detail` (seconds per km).
import type { UnitPreference } from "./api/types";
import { distanceUnitLabel, formatPaceValue } from "./formatDistance";
import { formatMinPerKm } from "./runningStats";

const KM_PER_MILE = 1.609344;

export interface PacingText {
  title: string;
  /** Halves, first 2 km, last 2 km and the negative km splits; null when the detail has no
   * paces. */
  paces: string | null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function pacingText(
  detail: Record<string, unknown>,
  fallbackTitle: string,
  unit: UnitPreference,
): PacingText {
  const first = num(detail.first_half_s_per_km);
  const second = num(detail.second_half_s_per_km);
  if (first == null || second == null) return { title: fallbackTitle, paces: null };

  const perUnit = unit === "imperial" ? KM_PER_MILE : 1;
  const gap = `${formatMinPerKm((Math.abs(second - first) * perUnit) / 60)} /${distanceUnitLabel(unit)}`;
  let title: string;
  if (detail.split === "negative") {
    title = `Negative split: second half ${gap} faster than the first`;
  } else if (detail.split === "positive") {
    title = `Positive split: second half ${gap} slower than the first`;
  } else {
    title = "Even split: both halves at the same pace";
  }

  const parts = [
    `First half ${formatPaceValue(first, unit)}`,
    `second half ${formatPaceValue(second, unit)}`,
  ];
  const start = num(detail.start_s_per_km);
  const finish = num(detail.finish_s_per_km);
  if (start != null) parts.push(`first 2 km ${formatPaceValue(start, unit)}`);
  if (finish != null) parts.push(`last 2 km ${formatPaceValue(finish, unit)}`);
  const negativeKms = num(detail.negative_km_splits);
  const secondHalfKms = num(detail.second_half_km_count);
  if (negativeKms != null && secondHalfKms != null) {
    parts.push(`${negativeKms} of ${secondHalfKms} second-half km faster than the first half`);
  }
  return { title, paces: parts.join(" · ") };
}
