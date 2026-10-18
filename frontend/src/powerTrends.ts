// Insights' cycling "Power trends" tab (PowerTrendsChart.tsx): the cycling counterpart of Pace
// trends. One dot per ride -- its best 20-minute power -- and the trailing-90-day FTP line from
// GET /performance/cycling-power, in watts or W/kg. Power, not speed, because speed swings with
// wind, gradient, drafting and virtual rides; speed stays available as a fallback for rides
// without a power meter (SpeedTrendsChart.tsx).
import type { ActivitySummary, CyclingPowerPointOut } from "./api/types";

export type PowerUnit = "w" | "wkg";

export interface PowerTrendPoint {
  id: string;
  name: string | null;
  localDate: string;
  /** UTC midnight of `localDate`, for a time axis. */
  ts: number;
  watts: number;
  /** Null when no weight is known for that date. */
  wattsPerKg: number | null;
}

export interface FtpLinePoint {
  ts: number;
  ftpW: number;
  ftpWPerKg: number | null;
}

function utcTs(localDate: string): number {
  return Date.parse(`${localDate}T00:00:00Z`);
}

/** The weight in force on `localDate`: the history point on that date, else the latest before. */
function weightOn(localDate: string, history: CyclingPowerPointOut[]): number | null {
  let weight: number | null = null;
  for (const p of history) {
    if (p.as_of > localDate) break;
    if (p.weight_kg != null) weight = p.weight_kg;
  }
  return weight;
}

/** Rides with a best 20-minute power, oldest first. `history` (oldest first) supplies the weight
 * the FTP figures used on each date, so a dot's W/kg matches the FTP/kg line beside it. */
export function powerTrendPoints(
  rides: ActivitySummary[],
  history: CyclingPowerPointOut[],
): PowerTrendPoint[] {
  return rides
    .filter((a) => a.local_date != null && a.best_20min_power_w != null && a.best_20min_power_w > 0)
    .map((a) => {
      const watts = a.best_20min_power_w!;
      const weight = weightOn(a.local_date!, history);
      return {
        id: a.id,
        name: a.name,
        localDate: a.local_date!,
        ts: utcTs(a.local_date!),
        watts,
        wattsPerKg: weight ? watts / weight : null,
      };
    })
    .sort((x, y) => x.ts - y.ts);
}

/** The FTP line: one point per date the FTP was known (the 90-day window held 20 minutes). */
export function ftpLine(history: CyclingPowerPointOut[]): FtpLinePoint[] {
  return history
    .filter((p) => p.ftp_w != null)
    .map((p) => ({ ts: utcTs(p.as_of), ftpW: p.ftp_w!, ftpWPerKg: p.ftp_w_per_kg }));
}
