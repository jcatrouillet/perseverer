// The map explorer's legend (pages/MapExplorerPage.tsx). Points are coloured by sport family
// (metricStyle.ts: one tone per family, so running and cycling share blue), so the legend has one
// entry per colour actually on the map, naming the sports shown in that colour and how many
// points it covers. Built from the filtered points, so it always matches what is visible.
import type { Tone } from "./metricStyle";
import { sportStyle, toneColor } from "./metricStyle";

export interface MapLegendEntry {
  tone: Tone;
  color: string;
  /** "Running, cycling": the sports in this colour, most frequent first. */
  label: string;
  count: number;
}

function sportName(sport: string): string {
  return sport.replace(/_/g, " ").toLowerCase();
}

export function mapLegendEntries(points: { sport: string }[]): MapLegendEntry[] {
  const byTone = new Map<Tone, Map<string, number>>();
  for (const { sport } of points) {
    const tone = sportStyle(sport).tone;
    const sports = byTone.get(tone) ?? new Map<string, number>();
    sports.set(sport, (sports.get(sport) ?? 0) + 1);
    byTone.set(tone, sports);
  }
  const entries: MapLegendEntry[] = [];
  for (const [tone, sports] of byTone) {
    const ordered = [...sports.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    const names = ordered.map(([sport]) => sportName(sport)).join(", ");
    entries.push({
      tone,
      color: toneColor(tone),
      label: names.charAt(0).toUpperCase() + names.slice(1),
      count: ordered.reduce((sum, [, n]) => sum + n, 0),
    });
  }
  return entries.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}
