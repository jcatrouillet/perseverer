// Bouldering's per-route grade + completed/attempt status, reverse-engineered from two
// undocumented FIT `split_mesgs` fields -- see fit/parser.py's own comment for the full
// methodology (decoded against two real bouldering activities' own logged route sequences, 19
// routes in one and 8 in the other, pairing each "climb_active" split in order against the
// logged grade+status for that route -- both fields matched on all 27 routes with zero
// exceptions). The backend already de-offsets climb_grade to the real V-scale number and maps
// the raw result value to "attempt"/"completed"; this module just turns that flat SplitOut[]
// into one row per route and formats it for display.
import type { ClimbGradeBreakdownOut, SplitOut } from "./api/types";

/** "bouldering" specifically, not every `rock_climbing` sub_sport -- a real top-rope/sport-climb
 * activity might have genuine GPS-based laps and no climb_active splits at all, and shouldn't be
 * silently treated as bouldering just because it shares the parent sport. */
export function isBoulderingActivity(sport: string, subSport: string | null): boolean {
  return sport === "rock_climbing" && subSport === "bouldering";
}

export interface BoulderingRoute {
  /** The split's own real identity for the .../climb-routes/{split_index} endpoints -- not the
   * same thing as routeNumber below, which is purely a display position. */
  splitIndex: number;
  routeNumber: number;
  /** null for an ungraded route (Kaya's "v?") -- shown as "?" in the routes table, never counted
   * toward any grade statistic. */
  grade: number | null;
  /** A Kaya-sourced route's own label (name, else hold colour + wall); null for Garmin's. */
  name: string | null;
  result: string;
  durationS: number | null;
  avgHr: number | null;
  maxHr: number | null;
  /** True only for a route the athlete added by hand -- only these ever get a delete
   * affordance in the UI (see split.is_manual's own comment in db/schema.py). */
  isManual: boolean;
}

/** One entry per "climb_active" split, in FIT order (== attempt order) -- "climb_rest" splits
 * carry neither the grade nor the result field and are never routes in their own right. avg/max
 * HR (unlike grade/result) are also real on the *rest* split between routes, but this table
 * shows one row per route, so only the climb's own HR is surfaced here. */
export function boulderingRoutes(splits: SplitOut[]): BoulderingRoute[] {
  return splits
    // A graded Garmin route, or any Kaya-sourced one (which may be ungraded, "v?").
    .filter((s) => s.split_type === "climb_active" && (s.climb_grade != null || s.climb_name))
    .map((s, i) => ({
      splitIndex: s.split_index,
      routeNumber: i + 1,
      grade: s.climb_grade,
      name: s.climb_name ?? null,
      result: s.climb_result ?? "unknown",
      durationS: s.duration_s,
      avgHr: s.climb_avg_hr,
      maxHr: s.climb_max_hr,
      isManual: s.is_manual === true,
    }));
}

export function formatGrade(grade: number | null): string {
  return grade == null ? "V?" : `V${grade}`;
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

export interface ClimbSummary {
  routeCount: number;
  /** null when nothing in this activity was actually completed -- never a fabricated "V0" for a
   * session that was all attempts. */
  maxCompletedGrade: number | null;
  /** Sum of each route's own duration_s -- time actually climbing, excluding rest between
   * attempts. null when every route's duration is unknown (e.g. a session made entirely of
   * manually-added routes, which have no device timer behind them at all). */
  climbTimeS: number | null;
}

/** The "Climb" stats column on the activity detail page -- see ActivityStatsGrid.tsx. */
export function climbSummary(routes: BoulderingRoute[]): ClimbSummary {
  const completedGrades = routes
    .filter((r) => r.result === "completed")
    .map((r) => r.grade)
    .filter((g): g is number => g != null);
  const durations = routes.map((r) => r.durationS).filter((d): d is number => d != null);
  return {
    routeCount: routes.filter((r) => r.grade != null).length,
    maxCompletedGrade: completedGrades.length > 0 ? Math.max(...completedGrades) : null,
    climbTimeS: durations.length > 0 ? durations.reduce((sum, d) => sum + d, 0) : null,
  };
}

/** The per-grade attempted/completed distribution for `ClimbGradeChart` -- same shape as the
 * backend's own `GET /activities/climbing-summary` response, computed client-side here from one
 * activity's already-loaded `splits` instead (the activity detail page's own use case; the
 * period-summary page's use case reads the real aggregate endpoint instead, since that needs to
 * span however many sessions fall in the period, not just one activity's own routes). An
 * unconfirmed "unknown_<n>" raw result (see fit/parser.py) is counted conservatively as an
 * attempt, matching the backend's own choice, never assumed completed. Sorted ascending by
 * grade, matching the reference chart's own left-to-right easy-to-hard reading order. */
export function gradeBreakdownFromRoutes(routes: BoulderingRoute[]): ClimbGradeBreakdownOut[] {
  const byGrade = new Map<number, { attempted: number; completed: number }>();
  for (const r of routes) {
    if (r.grade == null) continue;
    const bucket = byGrade.get(r.grade) ?? { attempted: 0, completed: 0 };
    if (r.result === "completed") bucket.completed += 1;
    else bucket.attempted += 1;
    byGrade.set(r.grade, bucket);
  }
  return Array.from(byGrade.entries())
    .sort(([a], [b]) => a - b)
    .map(([grade, counts]) => ({ grade, ...counts }));
}
