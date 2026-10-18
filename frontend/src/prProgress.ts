// PR progress (PrProgressChart.tsx): the fastest-at-each-distance frontier, older records
// against the last year's. Runs: one best-VDOT run per week. Rides (the Insights sport switch):
// every ride with a power reading, plotted by its average power (not speed: wind, hills and
// drafting move speed), since rides have no VDOT and are few enough not to need thinning. Runs and
// rides are never mixed. `rank` (lower is better) orders both: a run's pace, a ride's negative
// power, so the same "no equally long or longer effort beats it" frontier serves either.
import type { ActivitySummary, InsightsSport } from "./api/types";
import { EARLIEST_PLAUSIBLE_DATE, isoDate, mondayOf, parseIsoDate } from "./dateUtils";
import { effectiveDurationS, isCyclingSport } from "./runningStats";

export interface PrPoint {
  activity: ActivitySummary;
  distanceKm: number;
  pace: number;
  speedKmh: number;
  /** A ride's average power in watts; null for a run. */
  powerW: number | null;
  /** Lower is better: pace for a run, minus the average power for a ride. */
  rank: number;
  /** null for a ride. */
  vdot: number | null;
  older: boolean;
}

/** Calendar-year anniversary, clamped to February 28 when today is leap day. */
export function yearAgo(today: string): string {
  const date = parseIsoDate(today);
  const month = date.getUTCMonth();
  date.setUTCFullYear(date.getUTCFullYear() - 1);
  if (date.getUTCMonth() !== month) date.setUTCDate(0);
  return isoDate(date);
}

export function prProgress(
  activities: ActivitySummary[],
  today: string,
  sport: InsightsSport = "running",
) {
  const cutoff = yearAgo(today);
  const weekly = new Map<string, ActivitySummary>();
  const cycling = sport === "cycling";
  // Stable ties: earliest run, then id. Preserve full VDOT precision when selecting winners.
  const eligible = activities
    .filter(
      (a) =>
        (cycling ? isCyclingSport(a.sport) && (a.avg_power_w ?? 0) > 0 : a.sport === "running") &&
        a.local_date != null &&
        a.local_date >= EARLIEST_PLAUSIBLE_DATE &&
        a.local_date <= today &&
        (cycling || (a.vdot != null && Number.isFinite(a.vdot) && a.vdot > 0)) &&
        a.distance_m != null &&
        Number.isFinite(a.distance_m) &&
        a.distance_m > 0 &&
        Number.isFinite(effectiveDurationS(a)) &&
        (effectiveDurationS(a) ?? 0) > 0,
    )
    .sort((a, b) => a.local_date!.localeCompare(b.local_date!) || a.id.localeCompare(b.id));
  if (!cycling) {
    for (const activity of eligible) {
      const week = isoDate(mondayOf(parseIsoDate(activity.local_date!)));
      if (activity.vdot! > (weekly.get(week)?.vdot ?? -Infinity)) weekly.set(week, activity);
    }
  }
  const selected = cycling ? eligible : [...weekly.values()];
  const points: PrPoint[] = selected
    .map((activity) => {
      const pace = effectiveDurationS(activity)! / (activity.distance_m! / 1000);
      return {
        activity,
        distanceKm: activity.distance_m! / 1000,
        pace,
        speedKmh: 3600 / pace,
        powerW: cycling ? activity.avg_power_w! : null,
        rank: cycling ? -activity.avg_power_w! : pace,
        vdot: activity.vdot ?? null,
        older: activity.local_date! <= cutoff,
      };
    })
    .sort((a, b) => a.distanceKm - b.distanceKm);
  const older = points.filter((p) => p.older);
  const recent = points.filter((p) => !p.older);
  const baseline = recordFrontier(older);
  const combined = recordFrontier(points);
  // Split at every exact record distance. Blue only covers intervals that strictly improve
  // the observed older envelope; never extrapolate beyond the longest older run.
  const boundaries = [
    ...new Set([0, ...baseline.map((p) => p.distanceKm), ...combined.map((p) => p.distanceKm)]),
  ].sort((a, b) => a - b);
  const improvements: {
    distanceKm: number;
    pace: number;
    speedKmh: number;
    powerW: number | null;
  }[][] = [];
  for (let i = 1; i < boundaries.length; i++) {
    const left = boundaries[i - 1];
    const right = boundaries[i];
    const old = baseline.find((p) => p.distanceKm >= right);
    const best = combined.find((p) => p.distanceKm >= right);
    if (!old || !best || best.older || best.rank >= old.rank - 1e-9) continue;
    const last = improvements.at(-1);
    const at = (distanceKm: number) => ({
      distanceKm,
      pace: best.pace,
      speedKmh: best.speedKmh,
      powerW: best.powerW,
    });
    if (last?.at(-1)?.distanceKm === left) {
      last.push(at(left), at(right));
    } else {
      improvements.push([at(left), at(right)]);
    }
  }
  return { cutoff, older, recent, baseline, improvements, combined };
}

/** Exact-distance frontier: no equally long or longer run is as fast. Prefer older on
 * an exact tie so an unchanged record never becomes a blue improvement. */
function recordFrontier(points: PrPoint[]): PrPoint[] {
  const sorted = [...points].sort(
    (a, b) =>
      b.distanceKm - a.distanceKm ||
      a.rank - b.rank ||
      Number(b.older) - Number(a.older) ||
      a.activity.id.localeCompare(b.activity.id),
  );
  const frontier: PrPoint[] = [];
  let fastest = Infinity;
  for (const point of sorted) {
    if (point.rank < fastest - 1e-9 && frontier.at(-1)?.distanceKm !== point.distanceKm) {
      frontier.push(point);
      fastest = point.rank;
    }
  }
  return frontier.reverse();
}
