// Pure grouping/filtering helpers for InsightsPage -- kept separate from the component so the
// logic is testable without rendering, matching this codebase's existing split (yearStats.ts,
// runningStats.ts) between pure view-model helpers and the components that use them.
import type { InsightOut } from "./api/types";

export const WINDOWS = ["30d", "90d", "180d", "year", "365d"] as const;

export const WINDOW_LABELS: Record<string, string> = {
  "30d": "Last 30 days",
  "90d": "Last 90 days",
  "180d": "Last 180 days",
  year: "This year",
  "365d": "Last 12 months",
};

// Streak/load/health insights that aren't tied to one of the five standard windows use
// window="current" -- always shown, regardless of which window the user has selected, since
// they represent "right now" rather than a fixed lookback period.
export const CURRENT_WINDOW = "current";

export const KIND_LABELS: Record<string, string> = {
  streak: "Streaks & consistency",
  load: "Load & recovery",
  health: "Health & wellness",
  pb: "Personal bests",
  effort: "Notable efforts",
};

export const KIND_ORDER = ["streak", "load", "health", "pb", "effort"];

export function visibleInsights(insights: InsightOut[], window: string): InsightOut[] {
  return insights.filter((i) => i.window === CURRENT_WINDOW || i.window === window);
}

export function groupByKind(insights: InsightOut[]): Map<string, InsightOut[]> {
  const groups = new Map<string, InsightOut[]>();
  for (const i of insights) {
    const existing = groups.get(i.kind);
    if (existing) {
      existing.push(i);
    } else {
      groups.set(i.kind, [i]);
    }
  }
  return groups;
}
