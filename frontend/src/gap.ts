// Grade Adjusted Pace -- the equivalent flat-ground pace that represents the same physiological
// effort as running at a given grade. No raw field exists anywhere in this archive (confirmed:
// zero GAP/Performance-Condition/Stamina fields in FIT or JSON sources -- ADR 0010's real-data
// inventory), so this is computed client-side from elevation+distance stream data. Shared by the
// per-km splits panel (ActivityRouteMap/SplitsTable), the GAP chart panel, and the Intervals
// table's per-lap GAP column (ActivityDetailPage.tsx), so all three read the same effort model.
//
// Uphill/flat uses the energy-cost-of-running model from Minetti et al. (2002), "Energy cost of
// walking and running at extreme uphill and downhill slopes" -- this was confirmed (by directly
// reading Strava Engineering's own posts, not assumed) to be literally Strava's own original GAP
// model. Cost of running C(i), in J/(kg*m), as a function of grade i (fraction, e.g. 0.1 = 10%
// uphill):
//   C(i) = 155.4*i^5 - 30.4*i^4 - 43.3*i^3 + 46.3*i^2 + 19.5*i + 3.6
// Power (J/(kg*s)) at grade i and speed v is C(i)*v. Grade-adjusted speed is the flat-ground
// speed producing the same power: C(0)*v_gap = C(i)*v_actual, so v_gap = v_actual * C(i)/C(0).
// Equivalently, in pace (time/distance) terms: pace_gap = pace_actual * C(0)/C(i). Strava's own
// "An Improved GAP Model" (2017) post says uphill agreement between Minetti and their newer,
// empirically-fit model is close ("almost equal", shifted ~2%), so uphill/flat keeps pure
// Minetti unmodified.
//
// Downhill is different: that same 2017 post says Minetti overcorrects badly on downhills
// (predicting a runner needs to run a steep downhill at up to 2x actual speed to match flat
// effort, bottoming out around -18% grade) compared to their replacement model (bottoming out
// at a much gentler ~1.14x around -9%, then easing back to no adjustment at all by -18%) --
// confirmed by reading that post directly, not assumed. Strava has never published the new
// model's exact coefficients (proprietary, fit from millions of runs' heart-rate data, later
// patented as US 11,623,121) -- not even intervals.icu's own from-scratch GAP implementation
// matches it exactly (confirmed via their forum: its own author found unexplained mismatches
// against real Strava GAP and speculated Strava's model had moved beyond what they'd published).
// So rather than inventing unpublished numbers, this reproduces only the three concrete points
// Strava's own chart discloses -- flat (0%, factor 1.0), the dip (-9%, factor 0.88), and the
// recovery (-18%, factor 1.0) -- as a smooth bump, holding flat (no adjustment) beyond -18% where
// no further published data exists.

const FLAT_COST = 3.6; // C(0)

// The polynomial is only validated by Minetti's own data out to roughly +-45% grade; clamping
// keeps a single noisy GPS-elevation spike from extrapolating into a nonsensical multiplier.
const MAX_GRADE = 0.45;

function costOfRunning(gradeFraction: number): number {
  const i = Math.max(-MAX_GRADE, Math.min(MAX_GRADE, gradeFraction));
  return 155.4 * i ** 5 - 30.4 * i ** 4 - 43.3 * i ** 3 + 46.3 * i ** 2 + 19.5 * i + FLAT_COST;
}

// The three points Strava's own "An Improved GAP Model" comparison chart disclosed for their
// post-2017 downhill curve -- see the module docstring above for why these three and not a full
// refit. Expressed as a *speed* multiplier f(grade) (matching the chart's own convention:
// v_gap = v_actual * f(grade)), so pace_gap = pace_actual / f(grade). The dip sits exactly at
// half of DOWNHILL_RECOVERY_GRADE (-9% = -18%/2) by construction of the sine bump below, so
// there's no separate "dip grade" constant to carry -- it falls out of the formula itself.
const DOWNHILL_DIP_FACTOR = 0.88;
const DOWNHILL_RECOVERY_GRADE = -0.18;

/** Smooth bump through (0, 1.0) -> (DOWNHILL_RECOVERY_GRADE / 2, DOWNHILL_DIP_FACTOR) ->
 * (DOWNHILL_RECOVERY_GRADE, 1.0), flat at 1.0 beyond the recovery point. Only ever called with
 * gradeFraction <= 0. */
function downhillSpeedFactor(gradeFraction: number): number {
  if (gradeFraction <= DOWNHILL_RECOVERY_GRADE) return 1;
  return (
    1 - (1 - DOWNHILL_DIP_FACTOR) * Math.sin((Math.PI * gradeFraction) / DOWNHILL_RECOVERY_GRADE)
  );
}

/** Grade-adjusted pace (minutes/km) for a segment run at `actualPaceMinPerKm` over `gradeFraction`
 * (elevation change / horizontal distance, e.g. 0.05 for a 5% uphill, -0.05 for a 5% downhill).
 * Returns `actualPaceMinPerKm` unchanged at zero grade. */
export function gradeAdjustedPaceMinPerKm(
  actualPaceMinPerKm: number,
  gradeFraction: number,
): number {
  if (gradeFraction < 0) {
    return actualPaceMinPerKm / downhillSpeedFactor(gradeFraction);
  }
  return actualPaceMinPerKm * (FLAT_COST / costOfRunning(gradeFraction));
}

// A point-by-point grade straight off consecutive GPS/barometric samples is dominated by noise
// (a single metre of elevation jitter over a couple of metres of distance reads as a cliff) --
// widening to a real horizontal window before taking the grade is what the splits panel gets for
// free by averaging over a whole kilometre, and what this per-point chart series needs to do
// explicitly instead.
const DEFAULT_HALF_WINDOW_M = 25;

function gradeAt(
  distanceM: (number | null)[],
  altitudeM: (number | null)[],
  i: number,
  halfWindowM: number,
): number | null {
  const center = distanceM[i];
  if (center == null) return null;

  let left = i;
  while (left > 0 && distanceM[left] != null && center - distanceM[left]! < halfWindowM) left--;
  let right = i;
  const n = distanceM.length;
  while (right < n - 1 && distanceM[right] != null && distanceM[right]! - center < halfWindowM) right++;

  const d0 = distanceM[left];
  const d1 = distanceM[right];
  const a0 = altitudeM[left];
  const a1 = altitudeM[right];
  if (d0 == null || d1 == null || a0 == null || a1 == null) return null;
  const span = d1 - d0;
  return span > 0 ? (a1 - a0) / span : null;
}

/** A per-point GAP series (minutes/km), parallel to `paceMinPerKm`, for the GAP chart panel --
 * the point-by-point counterpart to `computeKmSplits`' one-value-per-split approximation. Each
 * point's grade is smoothed over a `2*halfWindowM`-wide horizontal window centred on it. A point
 * is `null` wherever its own pace, or the window's distance/altitude data, isn't available. */
export function gapSeriesMinPerKm(
  distanceM: (number | null)[],
  altitudeM: (number | null)[],
  paceMinPerKm: (number | null)[],
  halfWindowM: number = DEFAULT_HALF_WINDOW_M,
): (number | null)[] {
  return paceMinPerKm.map((pace, i) => {
    if (pace == null) return null;
    const grade = gradeAt(distanceM, altitudeM, i, halfWindowM);
    return grade == null ? null : gradeAdjustedPaceMinPerKm(pace, grade);
  });
}

export interface LapForGap {
  start_time_utc: string;
  duration_s: number | null;
  moving_duration_s: number | null;
  distance_m: number | null;
}

function altitudeAtRawElapsedS(
  rawElapsedS: number[],
  altitudeM: (number | null)[],
  targetS: number,
): number | null {
  for (let i = 0; i < rawElapsedS.length; i++) {
    if (rawElapsedS[i]! < targetS) continue;
    if (i === 0) return altitudeM[0] ?? null;
    const a0 = altitudeM[i - 1];
    const a1 = altitudeM[i];
    if (a0 == null || a1 == null) return null;
    const prevT = rawElapsedS[i - 1]!;
    const span = rawElapsedS[i]! - prevT;
    const frac = span > 0 ? (targetS - prevT) / span : 0;
    return a0 + frac * (a1 - a0);
  }
  return altitudeM[altitudeM.length - 1] ?? null;
}

/** Grade-adjusted pace (minutes/km) for each lap in `laps`, one-value-per-lap in the same
 * one-average-grade-per-segment style as `splits.ts::buildSplit` -- just keyed by each lap's own
 * wall-clock time range (from its recorded `start_time_utc`) instead of a fixed distance
 * boundary. A lap's own *end* boundary is the next lap's `start_time_utc` (mirrors
 * ActivityCharts.tsx's own `lapBoundaries`, built the same way for its lap-band overlays) rather
 * than summing durations -- robust to a lap that paused mid-interval, where `duration_s`
 * (elapsed) and `moving_duration_s` (pause-excluded) diverge and neither alone locates the real
 * recorded end-of-lap sample. `streamStartTimeUtc` is the stream's own first timestamp, the same
 * reference point ActivityCharts.tsx converts lap start times against. Returns one entry per
 * lap, aligned by position; `null` wherever the stream has no altitude channel or a lap's own
 * distance/duration is missing. */
export function computeLapGapsMinPerKm(
  laps: LapForGap[],
  streamStartTimeUtc: string,
  rawElapsedS: number[],
  altitudeM: (number | null)[] | undefined,
): (number | null)[] {
  if (altitudeM == null || rawElapsedS.length === 0 || laps.length === 0) {
    return laps.map(() => null);
  }
  const startMs = new Date(streamStartTimeUtc).getTime();
  const lapStartS = laps.map((lap) => (new Date(lap.start_time_utc).getTime() - startMs) / 1000);
  const lastRawS = rawElapsedS[rawElapsedS.length - 1]!;

  return laps.map((lap, i) => {
    // moving_duration_s excludes any device pause within the lap -- see the identical fallback
    // and rationale on ActivityDetailPage.tsx's own Pace column, kept in sync with it here.
    const effectiveDurationS = lap.moving_duration_s ?? lap.duration_s;
    if (!effectiveDurationS || lap.distance_m == null || lap.distance_m <= 0) return null;

    const endS = i + 1 < laps.length ? lapStartS[i + 1]! : lastRawS;
    const startAlt = altitudeAtRawElapsedS(rawElapsedS, altitudeM, lapStartS[i]!);
    const endAlt = altitudeAtRawElapsedS(rawElapsedS, altitudeM, endS);
    if (startAlt == null || endAlt == null) return null;

    const elevChangeM = endAlt - startAlt;
    const paceMinPerKm = effectiveDurationS / 60 / (lap.distance_m / 1000);
    return gradeAdjustedPaceMinPerKm(paceMinPerKm, elevChangeM / lap.distance_m);
  });
}
