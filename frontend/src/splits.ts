// Real "every km" splits, computed client-side from the stream's cumulative `distance_m`
// channel -- the FIT `split` table is device-detected segment types (run/walk detection,
// structured-workout intervals), confirmed against the real archive to be a different concept,
// not per-km splits (see docs/adr/0011-phase-7-map-recaps-pwa.md). This is the same approach
// Strava's own Splits tab uses: interpolate the timestamp/elevation at each 1km distance
// boundary, one row per completed km plus a final partial row for the remainder.
import { gradeAdjustedPaceMinPerKm } from "./gap";

export interface KmSplit {
  /** 1-indexed split number. */
  km: number;
  /** Actual distance covered by this split -- 1000 for every split except a shorter final one. */
  distanceM: number;
  durationS: number;
  paceMinPerKm: number;
  /** null when the stream has no elevation channel. */
  gapMinPerKm: number | null;
  /** Net elevation change across the split (end altitude - start altitude), Strava's own
   * "Elev" column convention -- not total ascent, so a rolling hill nets near zero. */
  elevChangeM: number | null;
  /** Index range into the source stream arrays, inclusive start/end -- for highlighting this
   * split's segment on the route map when its row is hovered. */
  startIndex: number;
  endIndex: number;
}

const KM_M = 1000;
/** A trailing remainder shorter than this isn't worth its own row -- avoids a "0.02 km split"
 * caused by the activity simply ending a few metres past the last full kilometre. */
const MIN_PARTIAL_SPLIT_M = 50;

interface Crossing {
  index: number;
  timeS: number;
  altitudeM: number | null;
}

/** Linear interpolation of elapsed time (and altitude, if present) at the point the cumulative
 * distance channel crosses `targetDistanceM`, walking forward from `fromIndex`. Returns the
 * bracketing index whose distance is >= target, so callers can slice `[prevIndex, index]` for
 * map highlighting. */
function interpolateAtDistance(
  distanceM: (number | null)[],
  elapsedS: number[],
  altitudeM: (number | null)[] | undefined,
  targetDistanceM: number,
  fromIndex: number,
): Crossing | null {
  for (let i = fromIndex; i < distanceM.length; i++) {
    const d = distanceM[i];
    if (d == null || d < targetDistanceM) continue;
    if (i === 0) return { index: 0, timeS: elapsedS[0]!, altitudeM: altitudeM?.[0] ?? null };
    const prevD = distanceM[i - 1];
    if (prevD == null) return { index: i, timeS: elapsedS[i]!, altitudeM: altitudeM?.[i] ?? null };
    const span = d - prevD;
    const frac = span > 0 ? (targetDistanceM - prevD) / span : 0;
    const timeS = elapsedS[i - 1]! + frac * (elapsedS[i]! - elapsedS[i - 1]!);
    const prevAlt = altitudeM?.[i - 1];
    const alt = altitudeM?.[i];
    const altitude = prevAlt != null && alt != null ? prevAlt + frac * (alt - prevAlt) : null;
    return { index: i, timeS, altitudeM: altitude };
  }
  return null;
}

/** Computes per-km splits from a stream's `distance_m` (cumulative metres), `elapsedS`
 * (seconds since activity start, same length/order as `distanceM`), and optional `altitudeM`.
 * `gapMinPerKm` uses each split's own average grade (elevation change / distance) as a
 * one-value-per-split approximation of the point-by-point GAP curve -- adequate for a summary
 * table row, distinct from the finer-grained GAP chart panel. A thin wrapper over
 * `computeSplitsAtInterval` fixed to whole kilometres, for the Splits table's own display. */
export function computeKmSplits(
  distanceM: (number | null)[],
  elapsedS: number[],
  altitudeM?: (number | null)[],
): KmSplit[] {
  return computeSplitsAtInterval(distanceM, elapsedS, KM_M, altitudeM);
}

/** Same interpolation as `computeKmSplits`, generalized to any fixed segment length -- e.g.
 * 250m for PaceVariabilityChart's finer-grained ring, where whole-km segments would be too few
 * and too coarse to show real pace texture within each kilometre. `KmSplit.km` becomes a plain
 * 1-indexed segment number rather than a literal kilometre count when `segmentM !== 1000`. */
export function computeSplitsAtInterval(
  distanceM: (number | null)[],
  elapsedS: number[],
  segmentM: number,
  altitudeM?: (number | null)[],
): KmSplit[] {
  if (distanceM.length === 0 || distanceM.length !== elapsedS.length || segmentM <= 0) return [];

  const totalDistance = [...distanceM].reverse().find((d) => d != null) ?? null;
  if (totalDistance == null || totalDistance <= 0) return [];

  const splits: KmSplit[] = [];
  let prevIndex = 0;
  let prevTimeS = elapsedS[0] ?? 0;
  let prevAltitude = altitudeM?.[0] ?? null;
  let prevDistance = 0;
  let segment = 1;

  while (segment * segmentM <= totalDistance) {
    const crossing = interpolateAtDistance(
      distanceM,
      elapsedS,
      altitudeM,
      segment * segmentM,
      prevIndex,
    );
    if (crossing == null) break;
    splits.push(
      buildSplit(
        segment,
        prevDistance,
        segment * segmentM,
        prevTimeS,
        crossing,
        prevIndex,
        prevAltitude,
      ),
    );
    prevIndex = crossing.index;
    prevTimeS = crossing.timeS;
    prevAltitude = crossing.altitudeM;
    prevDistance = segment * segmentM;
    segment += 1;
  }

  const remainder = totalDistance - prevDistance;
  if (remainder >= MIN_PARTIAL_SPLIT_M) {
    const lastIndex = distanceM.length - 1;
    const lastAltitude = altitudeM?.[lastIndex] ?? null;
    splits.push(
      buildSplit(
        segment,
        prevDistance,
        totalDistance,
        prevTimeS,
        { index: lastIndex, timeS: elapsedS[lastIndex]!, altitudeM: lastAltitude },
        prevIndex,
        prevAltitude,
      ),
    );
  }

  return splits;
}

function buildSplit(
  km: number,
  startDistanceM: number,
  endDistanceM: number,
  startTimeS: number,
  end: Crossing,
  startIndex: number,
  startAltitude: number | null,
): KmSplit {
  const distanceM = endDistanceM - startDistanceM;
  const durationS = end.timeS - startTimeS;
  const paceMinPerKm = durationS / 60 / (distanceM / 1000);
  const elevChangeM =
    startAltitude != null && end.altitudeM != null ? end.altitudeM - startAltitude : null;
  const gapMinPerKm =
    elevChangeM != null ? gradeAdjustedPaceMinPerKm(paceMinPerKm, elevChangeM / distanceM) : null;
  return {
    km,
    distanceM,
    durationS,
    paceMinPerKm,
    gapMinPerKm,
    elevChangeM,
    startIndex,
    endIndex: end.index,
  };
}
