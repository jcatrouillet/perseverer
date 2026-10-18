// Run comparison (pages/CompareRunsPage.tsx): the pure logic behind it.
//
// - Colour slots. Up to MAX_COMPARED runs, each in its own colour slot (--color-compare-N). A run
//   keeps its slot while others are added or removed, so its colour never changes under the
//   athlete's eyes; a new run takes the first free slot. The URL holds the slots in order
//   (`?ids=a,,c` when the second slot is free), so a comparison survives a reload and can be
//   bookmarked.
// - Series. Each run's stream is turned into the same channels the activity page charts (pace and
//   GAP in min/km, heart rate, cadence in steps per minute, elevation, power), with the same
//   clean-up: device pauses squeezed out of the time axis, isolated speed glitches dropped.
// - Alignment. Runs are sampled at different instants and distances, so every run is averaged
//   into the same COMPARE_BUCKETS buckets of distance (or of moving time) before charting: one row
//   per bucket, one column per run, which is what lets a single tooltip show every run at the
//   same point of the course.
import type { StreamResponse, UnitPreference } from "./api/types";
import { displayDistanceToMeters, metersToDisplayDistance } from "./formatDistance";
import { gapSeriesMinPerKm } from "./gap";
import {
  buildPauseCompressor,
  detectPauseGaps,
  rejectSpeedOutliers,
  streamSpeedValue,
} from "./runningStats";

/** The comparison covers one sport at a time: runs with runs, rides with rides. */
export type CompareSport = "running" | "cycling";

export interface CompareSportCopy {
  /** The `sport` filter the activity list API takes. */
  apiSport: string;
  /** "run" / "ride" */
  one: string;
  /** "runs" / "rides" */
  many: string;
  label: string;
}

export const COMPARE_SPORTS: Record<CompareSport, CompareSportCopy> = {
  running: { apiSport: "running", one: "run", many: "runs", label: "Running" },
  cycling: { apiSport: "cycling", one: "ride", many: "rides", label: "Cycling" },
};

export function parseCompareSport(raw: string | null): CompareSport {
  return raw === "cycling" ? "cycling" : "running";
}

/** The comparison page's address: the sport (omitted for running) and the chosen ids. */
export function compareHref(sport: CompareSport, ids: string = ""): string {
  const parts: string[] = [];
  if (sport !== "running") parts.push(`sport=${sport}`);
  if (ids) parts.push(`ids=${ids}`);
  return parts.length > 0 ? `/activities/compare?${parts.join("&")}` : "/activities/compare";
}

export const MAX_COMPARED = 8;
export const COMPARE_BUCKETS = 300;

/** The CSS colour of a slot (0-based). */
export function compareColor(slot: number): string {
  return `var(--color-compare-${slot + 1})`;
}

/** Slots from the `ids` query parameter: "a,,c" -> ["a", null, "c"]. */
export function parseSlots(param: string | null): (string | null)[] {
  if (!param) return [];
  const slots = param
    .split(",")
    .slice(0, MAX_COMPARED)
    .map((id) => id.trim() || null);
  const seen = new Set<string>();
  return trimSlots(
    slots.map((id) => {
      if (id == null || seen.has(id)) return null;
      seen.add(id);
      return id;
    }),
  );
}

export function slotsToParam(slots: (string | null)[]): string {
  return trimSlots(slots)
    .map((id) => id ?? "")
    .join(",");
}

function trimSlots(slots: (string | null)[]): (string | null)[] {
  const out = [...slots];
  while (out.length > 0 && out[out.length - 1] == null) out.pop();
  return out;
}

/** Puts `id` in the first free slot; unchanged when it's already there or every slot is taken. */
export function addToSlots(slots: (string | null)[], id: string): (string | null)[] {
  if (slots.includes(id)) return slots;
  const free = slots.indexOf(null);
  if (free >= 0) return slots.map((s, i) => (i === free ? id : s));
  if (slots.length >= MAX_COMPARED) return slots;
  return [...slots, id];
}

export function removeFromSlots(slots: (string | null)[], id: string): (string | null)[] {
  return trimSlots(slots.map((s) => (s === id ? null : s)));
}

export type CompareChannel =
  "pace" | "speed" | "gap" | "heart_rate" | "cadence" | "altitude" | "power";
export type CompareAxis = "distance" | "time";

export interface RunSeries {
  /** Distance from the start, km (null where the stream has none). */
  distanceKm: (number | null)[];
  /** Moving time from the start, s (device pauses squeezed out). */
  timeS: number[];
  channels: Record<CompareChannel, (number | null)[]>;
}

/** One run's chart channels from its stream, cleaned the same way as the activity page. */
export function prepareRunSeries(
  stream: StreamResponse,
  compareSport: CompareSport = "running",
): RunSeries | null {
  if (stream.timestamps.length === 0) return null;
  const running = compareSport === "running";
  const sport = compareSport;
  const startMs = new Date(stream.timestamps[0]!).getTime();
  const rawElapsed = stream.timestamps.map((t) => (new Date(t).getTime() - startMs) / 1000);
  const pauses = detectPauseGaps(rawElapsed);
  const compress = buildPauseCompressor(rawElapsed, pauses);
  const timeS = rawElapsed.map(compress);

  const s = stream.series;
  const n = stream.timestamps.length;
  const empty: (number | null)[] = new Array<number | null>(n).fill(null);

  // The first sample, and the first one after each pause, are a standing start rather than a
  // real pace (see ActivityCharts.tsx); isolated glitches are dropped by rejectSpeedOutliers.
  const afterPause = new Set(pauses.gaps.map((g) => g.postGapIndex));
  afterPause.add(0);
  const speed = s.speed_mps
    ? rejectSpeedOutliers(s.speed_mps.map((v, i) => (afterPause.has(i) ? null : v)))
    : empty;
  // A run reads in pace (min/km), a ride in speed (km/h); grade-adjusted pace is a running model.
  const rate = speed.map((v) => streamSpeedValue(sport, v));
  const pace = running ? rate : empty;
  const gap =
    running && s.distance_m && s.altitude_m
      ? gapSeriesMinPerKm(s.distance_m, s.altitude_m, rate)
      : empty;

  const firstDistance = s.distance_m?.find((v) => v != null) ?? 0;
  return {
    distanceKm: s.distance_m
      ? s.distance_m.map((v) => (v == null ? null : (v - firstDistance) / 1000))
      : empty,
    timeS,
    channels: {
      pace,
      speed: running ? empty : rate,
      gap,
      heart_rate: s.heart_rate ?? empty,
      // A runner's cadence is stored per foot (steps per minute is twice that); a rider's is rpm.
      cadence: s.cadence ? s.cadence.map((v) => (v == null ? null : running ? v * 2 : v)) : empty,
      altitude: s.altitude_m ?? empty,
      power: s.power ?? empty,
    },
  };
}

export interface AlignedRow {
  /** Bucket start on the shared axis: km or seconds. */
  x: number;
  /** One value per compared run, keyed "s0".."s7" by colour slot; null when the run has none. */
  [slot: string]: number | null;
}

/** Every run averaged into COMPARE_BUCKETS buckets of the shared axis, for one channel. A run
 * shorter than the longest simply has no values past its own end. */
export function alignRuns(
  runs: { slot: number; series: RunSeries }[],
  channel: CompareChannel,
  axis: CompareAxis,
  buckets: number = COMPARE_BUCKETS,
): AlignedRow[] {
  const xsOf = (r: RunSeries) => (axis === "distance" ? r.distanceKm : r.timeS);
  let maxX = 0;
  for (const { series } of runs) {
    for (const x of xsOf(series)) if (x != null && x > maxX) maxX = x;
  }
  if (maxX <= 0) return [];
  const width = maxX / buckets;
  const rows: AlignedRow[] = Array.from({ length: buckets }, (_, i) => ({ x: i * width }));

  for (const { slot, series } of runs) {
    const sums = new Array<number>(buckets).fill(0);
    const counts = new Array<number>(buckets).fill(0);
    const xs = xsOf(series);
    const values = series.channels[channel];
    for (let i = 0; i < xs.length; i++) {
      const x = xs[i];
      const v = values[i];
      if (x == null || v == null || !Number.isFinite(v)) continue;
      const b = Math.min(buckets - 1, Math.floor(x / width));
      sums[b] += v;
      counts[b] += 1;
    }
    const key = `s${slot}`;
    rows.forEach((row, b) => {
      row[key] = counts[b]! > 0 ? sums[b]! / counts[b]! : null;
    });
  }
  return rows;
}

/** Whether any compared run has data for this channel (an absent power meter, say). */
export function channelHasData(runs: { series: RunSeries }[], channel: CompareChannel): boolean {
  return runs.some(({ series }) => series.channels[channel].some((v) => v != null));
}

/** "Similar distance" for the comparison's suggestion: the same whole kilometre (or mile, with
 * the miles setting), rounded down -- a 32.02 km run is compared with runs from 32.00 to 32.99 km.
 * `lowM` is included and `highM` excluded. Null for a run under one whole unit. */
export function similarDistanceBand(
  distanceM: number,
  unit: UnitPreference,
): { whole: number; lowM: number; highM: number } | null {
  // A hair of tolerance, so a run stored as 9999.9999 m still counts as 10 km.
  const whole = Math.floor(metersToDisplayDistance(distanceM, unit) + 1e-6);
  if (whole < 1) return null;
  return {
    whole,
    lowM: displayDistanceToMeters(whole, unit),
    highM: displayDistanceToMeters(whole + 1, unit),
  };
}

/** A ride's "similar distance" band: within 10% either way of its length, since rides span far
 * too wide a range for a whole-kilometre band to find anything. `whole` is the ride's own length
 * in the display unit, rounded, for the label ("about 64 km"). Null for a ride under one unit. */
export function similarRideBand(
  distanceM: number,
  unit: UnitPreference,
): { whole: number; lowM: number; highM: number } | null {
  const whole = Math.round(metersToDisplayDistance(distanceM, unit));
  if (whole < 1) return null;
  return { whole, lowM: distanceM * 0.9, highM: distanceM * 1.1 };
}

/** Average moving speed, km/h; null without a distance or a time. */
export function averageSpeedKmhOf(run: {
  distance_m: number | null;
  moving_duration_s: number | null;
  duration_s: number | null;
}): number | null {
  const seconds = run.moving_duration_s ?? run.duration_s;
  if (!run.distance_m || run.distance_m <= 0 || !seconds || seconds <= 0) return null;
  return run.distance_m / 1000 / (seconds / 3600);
}

/** Average moving pace, seconds per km; null without a distance or a time. */
export function averagePaceSPerKm(run: {
  distance_m: number | null;
  moving_duration_s: number | null;
  duration_s: number | null;
}): number | null {
  const seconds = run.moving_duration_s ?? run.duration_s;
  if (!run.distance_m || run.distance_m <= 0 || !seconds || seconds <= 0) return null;
  return seconds / (run.distance_m / 1000);
}

/** The run to suggest comparing `selected` with: the fastest other run in the same distance band
 * (`similarDistanceBand`, by average moving pace), and whether `selected` itself is faster than all of them -- then the
 * suggestion is the next fastest. Null when there's no other similar run with a pace. */
export function fastestSimilarRun<
  T extends {
    id: string;
    distance_m: number | null;
    moving_duration_s: number | null;
    duration_s: number | null;
  },
>(
  selected: T,
  candidates: T[],
  band: { lowM: number; highM: number },
  compareSport: CompareSport = "running",
): { run: T; selectedIsFastest: boolean } | null {
  const low = band.lowM;
  const high = band.highM;
  // Lower is better for both: a run's pace, a ride's negative speed.
  const score = (run: T): number | null => {
    if (compareSport === "running") return averagePaceSPerKm(run);
    const speed = averageSpeedKmhOf(run);
    return speed == null ? null : -speed;
  };
  let best: { run: T; score: number } | null = null;
  for (const run of candidates) {
    if (run.id === selected.id || run.distance_m == null) continue;
    if (run.distance_m < low || run.distance_m >= high) continue;
    const s = score(run);
    if (s != null && (best == null || s < best.score)) best = { run, score: s };
  }
  if (best == null) return null;
  const selectedScore = score(selected);
  return {
    run: best.run,
    selectedIsFastest: selectedScore != null && selectedScore < best.score,
  };
}
