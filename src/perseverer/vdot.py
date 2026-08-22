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
