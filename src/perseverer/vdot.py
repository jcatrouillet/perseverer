"""VDOT (Daniels-Gilbert running performance index) -- a single number derived from a run's
pace and duration that lets you compare effort quality across different distances, the way a
race-equivalent "fitness score" does. Formula verified against the published equations (Daniels
& Gilbert, "Oxygen Power", 1979) rather than recalled from memory:

    VO2 (ml/kg/min) at velocity v (m/min):      VO2 = -4.60 + 0.182258*v + 0.000104*v^2
    %VO2max sustainable for duration t (min):   %VO2max = 0.8 + 0.1894393*exp(-0.012778*t)
                                                              + 0.2989558*exp(-0.1932605*t)
    VDOT = VO2 / %VO2max

`%VO2max` is only physically meaningful in (0, 1] -- solving the equation above shows it exceeds
1.0 for durations under roughly 11 minutes (a genuinely short/anaerobic effort this aerobic model
was never fit to), so `compute_vdot` rejects any activity where the computed %VO2max would exceed
1.0 rather than emit a number the model itself doesn't stand behind, instead of hardcoding an
unverified "valid range" from memory.

Fed a GAP-adjusted (grade-adjusted-pace) effective velocity rather than raw average pace, so a
hilly run doesn't score as a worse "performance" than the same effort on flat ground -- see
`compute_gap_factor`'s own docstring for why that's computed from the stream's distance/altitude
alone, deliberately never touching per-point timestamps.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Minetti et al. (2002) energy-cost-of-running polynomial -- same coefficients as
# frontend/src/gap.ts's own `costOfRunning`, kept in sync deliberately (see that file's
# docstring for the model and the C(0) flat-ground baseline).
_FLAT_COST = 3.6
_MAX_GRADE = 0.45
# A segment shorter than this makes grade = elevation_delta/distance blow up from GPS noise
# alone; too small a distance denominator, not a real slope. Below this it's excluded from the
# weighted average entirely rather than clamped, since a near-zero-distance segment shouldn't
# contribute a fictitious grade to the aggregate at all.
_MIN_SEGMENT_M = 3.0


def _cost_of_running(grade_fraction: float) -> float:
    i = max(-_MAX_GRADE, min(_MAX_GRADE, grade_fraction))
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + _FLAT_COST


def compute_gap_factor(
    distance_m: Sequence[float | None], altitude_m: Sequence[float | None]
) -> float | None:
    """A distance-weighted grade-adjustment ratio for the whole activity: `< 1` means the route
    was net harder than flat (e.g. uphill-heavy), so the grade-adjusted pace should run faster
    than the raw average pace; `> 1` means net easier (e.g. downhill-heavy). Multiply an actual
    average pace (seconds/metre) by this factor to get the grade-adjusted equivalent.

    Deliberately computed from consecutive points' own distance/altitude deltas only -- never
    from timestamps. A recorded device pause shows up as a large *time* gap with near-zero
    *distance*, which would corrupt a time-weighted average badly (the paused segment's real
    elapsed time would get attributed to almost no distance); distance-weighting a per-metre
    energy cost sidesteps that failure mode entirely, since a stationary pause simply contributes
    no distance to weight anything by.

    Returns `None` when there's no usable stream (fewer than 2 points, or every segment too
    short/missing data to compute a grade from) -- callers should fall back to an unadjusted
    (factor=1.0) VDOT rather than skip the activity outright.
    """
    if len(distance_m) != len(altitude_m) or len(distance_m) < 2:
        return None

    weighted_cost_sum = 0.0
    distance_sum = 0.0
    for i in range(len(distance_m) - 1):
        d0, d1 = distance_m[i], distance_m[i + 1]
        a0, a1 = altitude_m[i], altitude_m[i + 1]
        if d0 is None or d1 is None or a0 is None or a1 is None:
            continue
        segment_distance = d1 - d0
        if segment_distance < _MIN_SEGMENT_M:
            continue
        grade = (a1 - a0) / segment_distance
        weighted_cost_sum += segment_distance * _cost_of_running(grade)
        distance_sum += segment_distance

    if distance_sum <= 0:
        return None
    avg_cost = weighted_cost_sum / distance_sum
    return _FLAT_COST / avg_cost


def compute_vdot(
    distance_m: float | None, duration_s: float | None, gap_factor: float | None = None
) -> float | None:
    """VDOT for one run. `duration_s` should be moving time (pauses excluded), matching
    `activity.moving_duration_s` -- the %VO2max term models how long an effort was sustained, and
    a device pause isn't part of the effort. `gap_factor` (see `compute_gap_factor`) is optional;
    omitted or `None` computes an unadjusted VDOT from raw average pace.

    Returns `None` for non-positive inputs or when the computed %VO2max exceeds 1.0 (the
    duration was too short for this aerobic model to apply -- see module docstring).
    """
    if distance_m is None or duration_s is None or distance_m <= 0 or duration_s <= 0:
        return None

    duration_min = duration_s / 60
    pct_vo2max = (
        0.8
        + 0.1894393 * math.exp(-0.012778 * duration_min)
        + 0.2989558 * math.exp(-0.1932605 * duration_min)
    )
    if not (0 < pct_vo2max <= 1.0):
        return None

    raw_velocity_m_per_min = (distance_m / duration_s) * 60
    factor = gap_factor if gap_factor is not None else 1.0
    if factor <= 0:
        return None
    effective_velocity = raw_velocity_m_per_min / factor

    vo2 = -4.60 + 0.182258 * effective_velocity + 0.000104 * effective_velocity**2
    if vo2 <= 0:
        return None

    return vo2 / pct_vo2max


# --- Threshold pace + race-time prediction: extensions of the same Daniels-Gilbert model above,
# not a second formula -- deliberately never Garmin's own precomputed daily_lactate_threshold/
# daily_race_predictions fields (see performance_rollup.py's own docstring for why: shown
# alongside, never reconciled, same posture fitness.py already takes with Garmin's Training
# Readiness). Riegel's simpler power-law race-time formula was considered and rejected: research
# shows it systematically underestimates marathon time predicted from shorter races (by 10+
# minutes for half of runners in one study), while VDOT -- being grounded in the same aerobic
# model already verified above -- stays within ~1% (10k->half) to ~2-2.5% (5k->marathon) for
# trained runners. One model, extended twice, is also simpler to keep correct than two.


# A representative point within Daniels' own published "Threshold" (lactate/anaerobic threshold,
# "LT2"/"VT2"/"OBLA") pace range of 86-92% of vVO2max (velocity at VO2max) -- Daniels' Running
# Formula's own training-pace table. Verified by hand: at VDOT=50 this yields a threshold pace of
# ~4:15/km, matching publicly known VDOT=50 Threshold-pace tables. Cross-checked against two
# newer, running-specific studies rather than left to rest on Daniels' 1979 model alone: Fathi,
# Shahidi & Alhusaen Aga (2025, Int J Exercise Science 18(5):1381-1392) measured the second
# ventilatory threshold (RCP) at 89.6 ± 3.8% VO2max in trained runners (gas-exchange/visual-
# inspection method, n=12); Esteve-Lanao, Sellés-Pérez, Arévalo-Chico & Cejuela (2026, Sports
# 14(1):29) measured VT2 at 83.8-87.4% VO2peak across performance levels (n=1,411 endurance
# runners). 0.88 sits between the two -- inside Fathi's ~1SD band and just above Esteve-Lanao's
# own range -- close enough to both that a single representative point stays defensible rather
# than needing per-study branching; kept unchanged rather than shifted toward either single new
# study alone, since real downstream consumers already calibrate against it (race predictions,
# `hr_zones.py`'s zone 3/4 boundaries, `running_load.py`'s rTSS).
THRESHOLD_VO2MAX_FRACTION = 0.88

# The *aerobic* threshold ("LT1"/"VT1", the upper edge of easy/aerobic running, well below
# THRESHOLD_VO2MAX_FRACTION above) -- new, not previously computed anywhere in this codebase.
# 0.73 is where the same two studies above converge specifically on this threshold, not just the
# anaerobic one: Fathi et al. 2025 measured VT1 at 73.2 ± 4.1% VO2max (n=12 trained runners);
# Esteve-Lanao et al. 2026 measured VT1 at 67.5-73.4% VO2peak depending on performance level
# (n=1,411 endurance runners) -- 0.73 sits within both a fraction of a percent, an unusually tight
# agreement between an independent small gas-exchange study and a much larger multi-site one.
AEROBIC_THRESHOLD_VO2MAX_FRACTION = 0.73


def compute_threshold_pace_s_per_km(
    vdot: float | None, fraction: float = THRESHOLD_VO2MAX_FRACTION
) -> float | None:
    """Running pace for a given VDOT at `fraction` of VO2max (VDOT standing in for VO2max here,
    same substitution `compute_vdot` itself is built on) -- `fraction` defaults to
    `THRESHOLD_VO2MAX_FRACTION` (the anaerobic/lactate threshold), and the same function computes
    the aerobic threshold pace by passing `AEROBIC_THRESHOLD_VO2MAX_FRACTION` instead: one model,
    two representative points on it, not two formulas. Solving the module's own `VO2(v)`
    quadratic for `v`:

        0.000104*v^2 + 0.182258*v - (4.60 + fraction*vdot) = 0

    `a > 0` and `c < 0` for any realistic VDOT/fraction combination in either use, so the
    discriminant always exceeds `b^2` and exactly one positive root exists (the `+` branch) -- no
    branch-selection ambiguity to get wrong. Returns `None` for a non-positive/missing VDOT.
    """
    if vdot is None or vdot <= 0:
        return None

    a, b = 0.000104, 0.182258
    c = -(4.60 + fraction * vdot)
    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        return None
    velocity_m_per_min = (-b + math.sqrt(discriminant)) / (2 * a)
    if velocity_m_per_min <= 0:
        return None
    return 60_000 / velocity_m_per_min


# One entry per predicted distance -- the label is also this feature's own external vocabulary
# (API field suffixes, frontend chart labels), so a fifth distance later is a one-line addition
# here, not a scattered find-and-replace.
RACE_DISTANCES_M: dict[str, float] = {
    "5k": 5000.0,
    "10k": 10000.0,
    "half_marathon": 21097.5,
    "marathon": 42195.0,
}

# (fastest, slowest) duration bounds to bisect within, in seconds, per distance -- fastest sits
# just below world-record pace (so a real elite VDOT still resolves), slowest is a generous
# walking-pace ceiling. `predict_race_time_s` returns `None` rather than extrapolate past these.
_RACE_TIME_SEARCH_BOUNDS_S: dict[str, tuple[float, float]] = {
    "5k": (720.0, 5400.0),
    "10k": (1560.0, 10800.0),
    "half_marathon": (3420.0, 21600.0),
    "marathon": (7200.0, 43200.0),
}
_BISECTION_TOLERANCE_S = 1.0
_BISECTION_MAX_ITERATIONS = 100


def predict_race_time_s(distance_m: float, vdot: float | None) -> float | None:
    """Predicted race time for `distance_m` at the given VDOT, by bisection over duration until
    `compute_vdot(distance_m, T) == vdot` to within `_BISECTION_TOLERANCE_S`. `compute_vdot` is
    monotonically decreasing in duration for a fixed distance (confirmed numerically against this
    module's own implementation, not assumed) -- both velocity and %VO2max fall as duration
    grows, so VDOT has a unique root here and bisection is exact, not a heuristic.

    Returns `None` if `vdot` is missing/non-positive, or falls outside what's representable
    within the search bounds for this distance (faster than the fast bound's own elite-pace VDOT,
    or slower than the slow bound's) -- deliberately not extrapolated past bounds already wide
    enough to cover amateur-to-elite performances.
    """
    if vdot is None or vdot <= 0 or distance_m <= 0:
        return None

    # Search bounds are keyed by the same label RACE_DISTANCES_M uses -- find it by matching
    # distance_m, since callers pass a raw distance (e.g. from RACE_DISTANCES_M itself).
    label = next((lbl for lbl, m in RACE_DISTANCES_M.items() if m == distance_m), None)
    if label is None:
        return None
    lo, hi = _RACE_TIME_SEARCH_BOUNDS_S[label]

    vdot_at_lo = compute_vdot(distance_m, lo)
    vdot_at_hi = compute_vdot(distance_m, hi)
    if vdot_at_lo is None or vdot_at_hi is None:
        return None
    if vdot > vdot_at_lo or vdot < vdot_at_hi:
        return None  # faster than the fast bound, or slower than the slow bound

    for _ in range(_BISECTION_MAX_ITERATIONS):
        if hi - lo <= _BISECTION_TOLERANCE_S:
            break
        mid = (lo + hi) / 2
        vdot_at_mid = compute_vdot(distance_m, mid)
        if vdot_at_mid is None:
            return None
        if vdot_at_mid > vdot:
            lo = mid  # still faster than target -- need a longer (slower) duration
        else:
            hi = mid
    return (lo + hi) / 2
