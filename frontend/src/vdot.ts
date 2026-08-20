// VDOT (Daniels-Gilbert running performance index) forward formula and its race-time inverse --
// mirrors src/sporthealth/vdot.py::compute_vdot exactly (same published Daniels & Gilbert,
// "Oxygen Power" (1979) equations; kept in sync deliberately, same precedent as gap.ts's own
// mirroring of vdot.py's grade-adjustment math, see that file's docstring). The backend only
// ever computes VDOT forward (from a real run's own pace/duration); this adds the inverse --
// "what race time does this VDOT predict at a given distance" -- purely for the Pace Trends
// chart's own "cut N:NN off your estimated 5K" framing, which has no server-side use, so it
// lives here rather than as a new backend endpoint.

/** VO2 (ml/kg/min) at velocity v (m/min). */
function vo2AtVelocity(v: number): number {
  return -4.6 + 0.182258 * v + 0.000104 * v ** 2;
}

/** %VO2max sustainable for duration t (minutes). */
function pctVo2MaxForDuration(t: number): number {
  return 0.8 + 0.1894393 * Math.exp(-0.012778 * t) + 0.2989558 * Math.exp(-0.1932605 * t);
}

/** VDOT for a flat-ground run of `distanceM` in `durationS` -- the same forward formula as
 * vdot.py::compute_vdot, unadjusted for grade (gap_factor=1.0), matching how VDOT-to-race-time
 * tables are conventionally read. Returns null when %VO2max would exceed 1.0 (an effort too
 * short for this aerobic model, see vdot.py's own docstring) or for non-positive inputs. */
export function vdotForEffort(distanceM: number, durationS: number): number | null {
  if (distanceM <= 0 || durationS <= 0) return null;
  const durationMin = durationS / 60;
  const pctVo2max = pctVo2MaxForDuration(durationMin);
  if (!(pctVo2max > 0 && pctVo2max <= 1.0)) return null;
  const velocity = (distanceM / durationS) * 60;
  const vo2 = vo2AtVelocity(velocity);
  if (vo2 <= 0) return null;
  return vo2 / pctVo2max;
}

/** Inverts vdotForEffort: the flat-ground duration (seconds) at `distanceM` that a given VDOT
 * predicts. No closed form -- duration appears both directly (in %VO2max) and inversely (via
 * velocity = distance/duration) -- so this bisects over duration, exploiting that VDOT strictly
 * decreases as duration increases for a fixed distance (running the same distance slower is
 * always a lower performance score). Returns null when the VDOT isn't representable at this
 * distance within a 5-minute-to-6-hour search window (an implausible combination, e.g. a very
 * low VDOT at a very short distance). */
export function predictRaceTimeS(vdot: number, distanceM: number): number | null {
  if (vdot <= 0 || distanceM <= 0) return null;

  let lo = 300; // 5 minutes
  let hi = 6 * 3600; // 6 hours
  // Push both ends inward until they're inside the model's valid duration range (see
  // vdotForEffort): below `lo`, %VO2max exceeds 1.0 (too short/fast); above `hi` for a short
  // distance, the velocity is so low the linear VO2 term itself goes negative (too slow) --
  // either way, null can't be used as a bisection bracket endpoint.
  let vLo = vdotForEffort(distanceM, lo);
  while (vLo == null && lo < hi) {
    lo += 30;
    vLo = vdotForEffort(distanceM, lo);
  }
  let vHi = vdotForEffort(distanceM, hi);
  while (vHi == null && hi > lo) {
    hi -= 60;
    vHi = vdotForEffort(distanceM, hi);
  }
  if (vLo == null || vHi == null) return null;
  if (!(vHi <= vdot && vdot <= vLo)) return null;

  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    const vMid = vdotForEffort(distanceM, mid);
    if (vMid == null) {
      lo = mid; // too short to be valid -- the real root is further out
      continue;
    }
    if (vMid > vdot) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return (lo + hi) / 2;
}
