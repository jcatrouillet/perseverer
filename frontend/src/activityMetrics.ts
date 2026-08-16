// Pure helpers over `ActivityDetail.metrics` (the full per-activity EAV list GET
// /activities/{id} already returns -- see api/schemas/activities.py::ActivityMetricOut). No
// backend change needed for the Milestone C stats grid or time-in-zone chart: every field they
// need is already in that array, just not yet surfaced anywhere in the UI.
import type { ActivityMetricOut } from "./api/types";

export function metricValue(metrics: ActivityMetricOut[], key: string): number | null {
  return metrics.find((m) => m.metric_key === key)?.value_num ?? null;
}

/** Same alias-merge as api/routers/activities.py's `_aliased_metric_subquery` -- `keys` is
 * ordered by priority, not just membership. GPX/TCX-sourced Strava activities have no
 * fit.session.* keys at all (see ADR 0013); this lets a caller ask for a logical field without
 * knowing which source namespace populated it for a given activity. */
export function metricValueAliased(metrics: ActivityMetricOut[], keys: string[]): number | null {
  for (const key of keys) {
    const value = metricValue(metrics, key);
    if (value != null) return value;
  }
  return null;
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

/** Computes time-in-zone directly from an activity's own per-second HR stream against the
 * athlete's *own configured* zone boundaries (see hr_zones.py::compute_hr_zone_boundaries) --
 * used instead of `extractHrZones` (which reads the device's own baked-in zone breakdown)
 * whenever the athlete has set their zones, so "Time in zones" reflects the zones they actually
 * asked for rather than whatever was configured on the watch at recording time. Same `HrZone[]`
 * shape as `extractHrZones` so the chart's own rendering doesn't need to know which source it
 * came from -- only the zone numbering differs (Z1-Z5 here, vs. the device's own Z0-Z6 "time
 * below zone 1"/"in zone N and above" convention).
 *
 * Each recorded sample's own time is attributed to whichever zone its own HR reading falls in,
 * from the interval up to (not including) the *next* recorded sample -- the same "reading at the
 * start of the interval decides the interval's zone" convention a device's own zone tracking
 * uses. The final sample contributes no interval (there's no next timestamp to bound it).
 */
export function computeHrZonesFromStream(
  heartRate: (number | null)[],
  timestamps: string[],
  boundaries: [number, number, number, number],
): HrZone[] | null {
  if (heartRate.length === 0 || heartRate.length !== timestamps.length) return null;

  const secondsByZone = new Map<number, number>();
  for (let i = 0; i < heartRate.length - 1; i++) {
    const hr = heartRate[i];
    if (hr == null) continue;
    const dt = (new Date(timestamps[i + 1]!).getTime() - new Date(timestamps[i]!).getTime()) / 1000;
    if (dt <= 0) continue;
    let zone = 1;
    while (zone <= 4 && hr >= boundaries[zone - 1]) zone += 1;
    secondsByZone.set(zone, (secondsByZone.get(zone) ?? 0) + dt);
  }

  if (secondsByZone.size === 0) return null;

  // Always all 5 zones (unlike extractHrZones' device data, this scheme's zone count is fixed,
  // not read off a variable-length device report) -- a zone the athlete never reached during
  // this activity shows as a real "0m / 0%" row, not a missing one.
  const zones: HrZone[] = [];
  for (let zone = 1; zone <= 5; zone++) {
    zones.push({
      index: zone,
      seconds: secondsByZone.get(zone) ?? 0,
      lowBoundary: zone === 1 ? null : boundaries[zone - 2]!,
      highBoundary: zone === 5 ? null : boundaries[zone - 1]!,
    });
  }
  return zones;
}
