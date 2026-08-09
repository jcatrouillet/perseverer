// Pure helpers over `ActivityDetail.metrics` (the full per-activity EAV list GET
// /activities/{id} already returns -- see api/schemas/activities.py::ActivityMetricOut). No
// backend change needed for the Milestone C stats grid or time-in-zone chart: every field they
// need is already in that array, just not yet surfaced anywhere in the UI.
import type { ActivityMetricOut } from "./api/types";

export function metricValue(metrics: ActivityMetricOut[], key: string): number | null {
  return metrics.find((m) => m.metric_key === key)?.value_num ?? null;
}

export interface HrZone {
  index: number;
  seconds: number;
  lowBoundary: number | null;
  highBoundary: number | null;
}

/** Reassembles the `fit.time_in_zone.time_in_hr_zone_<N>` / `..hr_zone_high_boundary_<N>`
 * metrics (see fit/parser.py::_time_in_zone_metrics) back into one row per zone, in order.
 * Zone count is read from whatever's actually present, never assumed to be 5 or 6 -- a device
 * with a differently-configured zone count must not silently truncate or crash.
 *
 * Index 0 is "time below zone 1" (no configured lower bound, and its upper bound is zone 1's
 * boundary); the last zone has no upper bound (open-ended top zone). Returns null rather than
 * an empty array when the activity has no time-in-zone data at all, so a caller can
 * distinguish "nothing to show" from "an empty chart". */
export function extractHrZones(metrics: ActivityMetricOut[]): HrZone[] | null {
  const timeByIndex = new Map<number, number>();
  const boundaryByIndex = new Map<number, number>();
  const timePrefix = "fit.time_in_zone.time_in_hr_zone_";
  const boundaryPrefix = "fit.time_in_zone.hr_zone_high_boundary_";

  for (const m of metrics) {
    if (m.value_num == null) continue;
    if (m.metric_key.startsWith(timePrefix)) {
      timeByIndex.set(Number(m.metric_key.slice(timePrefix.length)), m.value_num);
    } else if (m.metric_key.startsWith(boundaryPrefix)) {
      boundaryByIndex.set(Number(m.metric_key.slice(boundaryPrefix.length)), m.value_num);
    }
  }

  if (timeByIndex.size === 0) return null;

  const maxIndex = Math.max(...timeByIndex.keys());
  const zones: HrZone[] = [];
  for (let i = 0; i <= maxIndex; i++) {
    const seconds = timeByIndex.get(i);
    if (seconds == null) continue;
    zones.push({
      index: i,
      seconds,
      lowBoundary: i === 0 ? null : (boundaryByIndex.get(i - 1) ?? null),
      highBoundary: boundaryByIndex.get(i) ?? null,
    });
  }
  return zones;
}

/** Label for one zone's bpm range: "< 89", "89 – 105", "175+". */
export function hrZoneRangeLabel(zone: HrZone): string {
  if (zone.lowBoundary == null && zone.highBoundary != null) return `< ${zone.highBoundary}`;
  if (zone.lowBoundary != null && zone.highBoundary == null) return `${zone.lowBoundary}+`;
  if (zone.lowBoundary != null && zone.highBoundary != null) {
    return `${zone.lowBoundary} – ${zone.highBoundary}`;
  }
  return "—";
}
