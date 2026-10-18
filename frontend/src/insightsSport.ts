// The Insights page's sport switch (InsightsPage.tsx): which sport every tab describes, and the
// words each tab uses for it. Runs are only ever compared with runs and rides with rides, so a
// tab reads its activities, bands, zones and curves for the one sport selected here.
import type { InsightsSport } from "./api/types";

export interface InsightsSportCopy {
  /** The `sport` filter the activity list API takes. */
  apiSport: string;
  /** "run" / "ride" */
  one: string;
  /** "runs" / "rides" */
  many: string;
  /** "Running" / "Cycling", for headings and the switch. */
  label: string;
}

export const INSIGHTS_SPORTS: Record<InsightsSport, InsightsSportCopy> = {
  running: { apiSport: "running", one: "run", many: "runs", label: "Running" },
  cycling: { apiSport: "cycling", one: "ride", many: "rides", label: "Cycling" },
};

/** Tabs that only exist for running: race predictions and race readiness are running models
 * (VDOT). The VO2max tab becomes "FTP & VO2max" for cycling (ride_power.py). */
export type InsightsTab =
  | "pace-trends"
  | "training-bands"
  | "pr-progress"
  | "race-predictions"
  | "race-readiness"
  | "pace-hr-zones"
  | "vo2max"
  | "performance-curve"
  | "eddington";

const RUNNING_ONLY_TABS: ReadonlySet<InsightsTab> = new Set(["race-predictions", "race-readiness"]);

export function tabAvailable(tab: InsightsTab, sport: InsightsSport): boolean {
  return sport === "running" || !RUNNING_ONLY_TABS.has(tab);
}

/** The tab labels, per sport where the cycling version measures something else. */
export function tabLabel(tab: InsightsTab, sport: InsightsSport): string {
  const cycling = sport === "cycling";
  switch (tab) {
    case "pace-trends":
      return cycling ? "Power trends" : "Pace trends";
    case "training-bands":
      return "Training bands";
    case "pr-progress":
      return "PR progress";
    case "race-predictions":
      return "Race predictions";
    case "race-readiness":
      return "Race Readiness";
    case "pace-hr-zones":
      return cycling ? "HR zones" : "Pace/HR Zones";
    case "vo2max":
      return cycling ? "FTP & VO2max" : "VO2max";
    case "performance-curve":
      return "Performance Curve";
    case "eddington":
      return "Eddington Number";
  }
}
