// Bouldering's per-route grade + completed/attempt status, reverse-engineered from two
// undocumented FIT `split_mesgs` fields -- see fit/parser.py's own comment for the full
// methodology (decoded against two real bouldering activities' own logged route sequences, 19
// routes in one and 8 in the other, pairing each "climb_active" split in order against the
// logged grade+status for that route -- both fields matched on all 27 routes with zero
// exceptions). The backend already de-offsets climb_grade to the real V-scale number and maps
// the raw result value to "attempt"/"completed"; this module just turns that flat SplitOut[]
// into one row per route and formats it for display.
import type { SplitOut } from "./api/types";

export interface BoulderingRoute {
  routeNumber: number;
  grade: number;
  result: string;
  durationS: number | null;
  avgHr: number | null;
  maxHr: number | null;
}

/** One entry per "climb_active" split, in FIT order (== attempt order) -- "climb_rest" splits
 * carry neither the grade nor the result field and are never routes in their own right. avg/max
 * HR (unlike grade/result) are also real on the *rest* split between routes, but this table
 * shows one row per route, so only the climb's own HR is surfaced here. */
export function boulderingRoutes(splits: SplitOut[]): BoulderingRoute[] {
  return splits
    .filter((s) => s.split_type === "climb_active" && s.climb_grade != null)
    .map((s, i) => ({
      routeNumber: i + 1,
      grade: s.climb_grade!,
      result: s.climb_result ?? "unknown",
      durationS: s.duration_s,
      avgHr: s.climb_avg_hr,
      maxHr: s.climb_max_hr,
    }));
}

export function formatGrade(grade: number): string {
  return `V${grade}`;
}

/** "completed"/"attempt" are the only two raw values confirmed against real data (see this
 * module's own header comment) -- anything else arrives as a literal "unknown_<n>" from the
 * backend and is shown as-is rather than guessed at, per this project's own "never invent a
 * plausible-looking value" rule. */
export function formatResult(result: string): string {
  if (result === "completed") return "Completed";
  if (result === "attempt") return "Attempt";
  return result;
}

export interface BoulderingSummary {
  totalRoutes: number;
  completedRoutes: number;
}

export function summarizeBoulderingRoutes(routes: BoulderingRoute[]): BoulderingSummary {
  return {
    totalRoutes: routes.length,
    completedRoutes: routes.filter((r) => r.result === "completed").length,
  };
}
