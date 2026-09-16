// Running Eddington number, per calendar year: the largest integer E such that the athlete
// completed at least E runs of at least E km each that year -- a classic cycling-logging
// statistic (VeloViewer and others use it for rides) applied here to running. Mathematically
// identical to the h-index: sort distances descending, E is the largest N whose Nth-largest
// value (1-indexed) is itself >= N.
//
// Pure client-side computation over the same full running-history fetch
// (`useAllActivities({ sport: "running" })`) InsightsPage.tsx's own Pace trends tab already
// performs -- no new backend endpoint needed, the same "fetch once, aggregate in the browser"
// precedent `runningStats.ts`'s own `bestVdot`/`personalRecords`/streak logic already
// establishes. Raw `distance_m`, not GAP-adjusted -- Eddington number is traditionally a
// real-distance-covered statistic, not an effort-adjusted one. Exact `sport === "running"`
// (via the API filter the caller already applies), matching this app's own established
// precedent of exact-match over `sport_family()` for Running-specific stats (see
// `RunningStats.tsx`/`CLAUDE.md`'s own note on why trail_running/track_running are excluded).
import type { ActivitySummary, UnitPreference } from "./api/types";
import { metersToDisplayDistance } from "./formatDistance";

export interface YearEddington {
  year: number;
  eddingtonNumber: number;
  totalRuns: number;
  /** How many of this year's runs are already >= (eddingtonNumber + 1) km -- always strictly
   * less than (eddingtonNumber + 1) itself, by the same invariant that makes eddingtonNumber the
   * *largest* such E; never more than eddingtonNumber. */
  runsTowardNext: number;
  /** How many more runs of at least (eddingtonNumber + 1) km are needed to raise the number by
   * one -- always >= 1 given the invariant above. */
  runsNeededForNext: number;
}

/** The largest N such that at least N of `distancesKm` are >= N. Empty input (or every value
 * below 1) returns 0. */
export function computeEddingtonNumber(distancesKm: number[]): number {
  const sorted = [...distancesKm].sort((a, b) => b - a);
  let e = 0;
  for (let i = 0; i < sorted.length; i++) {
    if (sorted[i]! >= i + 1) {
      e = i + 1;
    } else {
      break; // sorted descending, candidate (i+1) only grows -- every later i fails too.
    }
  }
  return e;
}

/** One entry per calendar year that has at least one qualifying run, most recent year first.
 * `unit` genuinely changes the computed number, not just its display -- an Eddington number is
 * defined in terms of a real distance unit (VeloViewer and others offer the same km-vs-mi
 * choice), so a mile-preferring athlete gets their real mile-based Eddington number here, not a
 * km-computed one just relabeled. */
export function computeYearlyEddington(
  activities: ActivitySummary[],
  unit: UnitPreference = "metric",
): YearEddington[] {
  const distancesKmByYear = new Map<number, number[]>();
  for (const a of activities) {
    if (a.local_date == null || a.distance_m == null || a.distance_m <= 0) continue;
    const year = Number(a.local_date.slice(0, 4));
    const list = distancesKmByYear.get(year) ?? [];
    list.push(metersToDisplayDistance(a.distance_m, unit));
    distancesKmByYear.set(year, list);
  }

  return [...distancesKmByYear.entries()]
    .sort(([a], [b]) => b - a)
    .map(([year, distances]) => {
      const eddingtonNumber = computeEddingtonNumber(distances);
      const runsTowardNext = distances.filter((d) => d >= eddingtonNumber + 1).length;
      return {
        year,
        eddingtonNumber,
        totalRuns: distances.length,
        runsTowardNext,
        runsNeededForNext: eddingtonNumber + 1 - runsTowardNext,
      };
    });
}

export interface EddingtonBar {
  km: number;
  count: number;
  /** Always equal to `km` -- the y=x reference line plotted alongside `count`, so the classic
   * VeloViewer-style chart can overlay both series on one Recharts `ComposedChart`. */
  diagonal: number;
}

/** One bar per integer km from 1 to the longest run (rounded up), each holding how many runs
 * were at least that far -- the classic Eddington bar chart, whose crossing point with the y=x
 * diagonal visually marks the Eddington number. A non-increasing step function by construction:
 * every run counted at km also counts at every smaller km. Empty input returns []. */
export function computeEddingtonBars(distancesKm: number[]): EddingtonBar[] {
  if (distancesKm.length === 0) return [];
  const maxKm = Math.ceil(Math.max(...distancesKm));
  const bars: EddingtonBar[] = [];
  for (let km = 1; km <= maxKm; km++) {
    bars.push({ km, count: distancesKm.filter((d) => d >= km).length, diagonal: km });
  }
  return bars;
}
