// Which monthly and yearly goals have just been reached, and which of those the athlete has not
// yet been congratulated for. A goal is reached when its progress is at 100% or more; the
// congratulation is shown once per goal, remembered in this browser.
import type {
  BoulderingGoalProgressOut,
  DurationGoalProgressOut,
  GoalProgressOut,
} from "./api/types";

const STORAGE_KEY = "perseverer_celebrated_goals";

export interface ReachedGoal {
  /** Stable per goal: kind and database id. */
  key: string;
  period: "month" | "year";
  /** "500 km running", "40 h all sports", "30 routes any grade". */
  title: string;
}

function sportName(sport: string | null): string {
  return sport == null ? "all sports" : sport.replace(/_/g, " ");
}

function formatHours(seconds: number): string {
  const hours = seconds / 3600;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
}

export function reachedGoals(
  period: "month" | "year",
  distance: GoalProgressOut[],
  duration: DurationGoalProgressOut[],
  bouldering: BoulderingGoalProgressOut[],
  formatDistance: (meters: number) => string,
): ReachedGoal[] {
  const out: ReachedGoal[] = [];
  for (const p of distance) {
    if (p.goal == null || (p.pct_complete ?? 0) < 1) continue;
    out.push({
      key: `distance:${p.goal.id}`,
      period,
      title: `${formatDistance(p.goal.target_distance_m)} ${sportName(p.goal.sport)}`,
    });
  }
  for (const p of duration) {
    if (p.pct_complete < 1) continue;
    out.push({
      key: `duration:${p.goal.id}`,
      period,
      title: `${formatHours(p.goal.target_duration_s)} ${sportName(p.goal.sport)}`,
    });
  }
  for (const p of bouldering) {
    if (p.pct_complete < 1) continue;
    const g = p.goal;
    const grade = g.grade == null ? "any grade" : `V${g.grade}${g.and_harder ? "+" : ""}`;
    out.push({ key: `bouldering:${g.id}`, period, title: `${g.target_count} routes ${grade}` });
  }
  return out;
}

export function loadCelebrated(): Set<string> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((k) => typeof k === "string") : []);
  } catch {
    return new Set();
  }
}

export function saveCelebrated(keys: Iterable<string>): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...new Set(keys)]));
  } catch {
    // Storage unavailable: the congratulation may show again, which is harmless.
  }
}

/** The reached goals the athlete has not been congratulated for yet. */
export function unseen(reached: ReachedGoal[], celebrated: Set<string>): ReachedGoal[] {
  return reached.filter((g) => !celebrated.has(g.key));
}
