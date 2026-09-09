"""Distance/duration/load estimate + a per-segment effort-zone breakdown for a scheduled
**running** workout (`sport == "running"`, `EXERCISE_SPORTS`/`PLACEHOLDER_SPORTS` don't have
pace/HR targets to build any of this from -- see `api/routers/planned_workouts.py`'s own
sport-gating). Kept separate from `workout_syntax.py`, which is deliberately DB-free/pure-text
parsing -- this module needs the athlete's own configured thresholds
(`athlete_running_load_config.threshold_pace_sec_per_km`, `athlete_hr_zone_config`).

Reuses rather than reinvents: `running_load.py::compute_running_tss` already implements the
Coggan-style `duration_hours * IF^2 * 100` formula for a *completed* run's rTSS, keyed off the
same `threshold_pace_sec_per_km` -- calling it per planned step keeps a scheduled workout's
"load" directly comparable to a finished run's own rTSS, rather than a second, unrelated number.
`workout_syntax.py::expand_repeat_groups`/`estimate_step_distance_m`/`step_target_speed_mps`
already do the repeat-expansion and distance/pace-speed plumbing -- this module only adds the
one further layer those don't have: an effort zone number and a load contribution per step.

**5 pace zones, relative to the athlete's own threshold speed** (Daniels-inspired training
zones -- easy/marathon/threshold/interval/repetition -- expressed as speed ratios rather than
Daniels' own %vVO2max, since threshold *speed*, not vVO2max, is the reference this app already
has calibrated via `athlete_running_load_config`). An explicit judgment call, same spirit as
`hr_zones.py`'s own documented fraction constants -- adjust if it doesn't feel right:

    Z1 (easy)       speed < 0.87x threshold
    Z2 (marathon)   0.87x <= speed < 0.94x
    Z3 (threshold)  0.94x <= speed < 1.00x
    Z4 (interval)   1.00x <= speed < 1.06x
    Z5 (repetition) speed >= 1.06x

HR-targeted steps share the same 1-5 numbering directly (an explicit `target_hr_zone` maps
1:1 -- both are a 5-rung easy-to-max effort progression, so no cross-conversion table is
needed); an absolute bpm target is bucketed into the same 5 zones via
`hr_zones.py::compute_hr_zone_boundaries`. A step with no target at all (or where the athlete
hasn't configured a threshold pace yet) falls back to a zone/IF implied by its own `intensity`
word, using `_CANONICAL_ZONE_IF`'s per-zone midpoint rather than a precise pace-derived IF.
"""

from __future__ import annotations

from dataclasses import dataclass

from perseverer.hr_zones import compute_hr_zone_boundaries
from perseverer.running_load import compute_running_tss
from perseverer.workout_syntax import (
    DEFAULT_ASSUMED_SPEED_MPS,
    ParsedStep,
    estimate_step_distance_m,
    expand_repeat_groups,
    step_target_speed_mps,
)

# Speed-ratio-to-threshold ceilings for zones 1-4 (zone 5 is open-ended above the last one) --
# see this module's own docstring for the reasoning.
_ZONE_SPEED_RATIO_CEILINGS = (0.87, 0.94, 1.00, 1.06)

# One representative intensity-factor per zone, used only when a step has no concrete pace/HR
# value to derive a continuous IF from (an untargeted step, or an HR-zone/bpm step where only
# the zone/bucket is known, not an exact speed). Each value sits inside its own zone's speed-
# ratio band above, so falling back to it never contradicts the zone a step was already placed
# in.
_CANONICAL_ZONE_IF = (0.80, 0.90, 0.97, 1.03, 1.12)

# DEFAULT_ASSUMED_SPEED_MPS (imported above) is the assumed speed for an "active"/no-intensity
# step with no target at all -- the same constant `workout_syntax.py` already uses for its own
# distance<->duration estimate, so this module's zone/load number stays internally consistent
# with the duration/distance it's computed alongside rather than picking a second, different
# assumption.


@dataclass
class StepEstimate:
    duration_s: float
    distance_m: float
    zone: int | None  # 1-5, or None when no zone could be determined (e.g. a rest step)
    load: float | None  # Coggan-style rTSS contribution, or None alongside an undetermined zone
    # The continuous speed-to-threshold (or canonical-zone-IF) ratio behind `zone` -- exposed
    # separately because `zone` alone is too coarse for the frontend's load bar to draw from: two
    # steps can land in the same 1-5 zone bucket (e.g. 5:10-5:30/km and 4:50-5:15/km both landing
    # in zone 2 for a fast-threshold athlete) while still being a real, athlete-visible pace
    # difference the bar should show as different column heights, not identical ones. `None` iff
    # `zone` is also `None` (no target resolvable, or no threshold pace configured at all).
    intensity_factor: float | None


@dataclass
class WorkoutEstimate:
    duration_s: float
    distance_m: float
    load: float | None  # None only when threshold_pace_sec_per_km isn't configured at all
    segments: list[StepEstimate]


def _zone_for_speed_ratio(ratio: float) -> int:
    for zone, ceiling in enumerate(_ZONE_SPEED_RATIO_CEILINGS, start=1):
        if ratio < ceiling:
            return zone
    return 5


def _zone_for_hr_bpm(bpm: float, boundaries: tuple[int, int, int, int]) -> int:
    zone1_high, zone2_high, zone3_high, zone4_high = boundaries
    if bpm <= zone1_high:
        return 1
    if bpm <= zone2_high:
        return 2
    if bpm <= zone3_high:
        return 3
    if bpm <= zone4_high:
        return 4
    return 5


def estimate_step(
    step: ParsedStep,
    *,
    threshold_pace_sec_per_km: float | None,
    threshold_hr_bpm: float | None,
    hr_zone_boundaries: tuple[int, int, int, int] | None,
) -> StepEstimate:
    """One (already-repeat-expanded) step's own duration/distance/zone/load -- see this module's
    docstring for the per-target-type rules. `hr_zone_boundaries` is
    `hr_zones.compute_hr_zone_boundaries(...)`'s own return shape, precomputed once per workout
    by the caller rather than recomputed per step."""
    duration_s = 0.0
    if step.duration_time_s is not None:
        duration_s = step.duration_time_s
    elif step.duration_distance_m is not None:
        speed = step_target_speed_mps(step) or DEFAULT_ASSUMED_SPEED_MPS
        duration_s = step.duration_distance_m / speed if speed > 0 else 0.0
    distance_m = estimate_step_distance_m(step)

    threshold_speed_mps = (
        1000.0 / threshold_pace_sec_per_km
        if threshold_pace_sec_per_km is not None and threshold_pace_sec_per_km > 0
        else None
    )

    zone: int | None = None
    # A concrete speed, when we have one -- feeds compute_running_tss directly.
    speed_for_load: float | None = None

    target_speed = step_target_speed_mps(step)
    if target_speed is not None and threshold_speed_mps is not None:
        zone = _zone_for_speed_ratio(target_speed / threshold_speed_mps)
        speed_for_load = target_speed
    elif step.target_type == "heart_rate" and step.target_hr_zone is not None:
        zone = max(1, min(5, step.target_hr_zone))
    elif (
        step.target_type == "heart_rate"
        and step.target_low is not None
        and step.target_high is not None
        and hr_zone_boundaries is not None
    ):
        zone = _zone_for_hr_bpm((step.target_low + step.target_high) / 2, hr_zone_boundaries)
    elif step.intensity in ("warmup", "cooldown", "recovery"):
        zone = 1
    elif step.intensity == "rest":
        zone = None  # excluded from training stress entirely, still occupies its own width
    elif step.target_type is None and threshold_speed_mps is not None:
        # No target at all (not even one we failed to resolve into a zone) -- fall back to the
        # same assumed speed workout_syntax.py itself uses for a distance-only step's own
        # duration estimate, so this module's zone/load stays consistent with the duration/
        # distance computed alongside it. Deliberately NOT applied to an HR-targeted step that
        # merely couldn't be bucketed (e.g. no athlete_hr_zone_config) -- silently guessing a
        # pace-based zone for a step the athlete explicitly gave an HR target to would be a real
        # mislabel (a hard HR interval could read as an easy zone), not just an approximation;
        # such a step is left with zone=None (rendered neutral) instead.
        zone = _zone_for_speed_ratio(DEFAULT_ASSUMED_SPEED_MPS / threshold_speed_mps)
        speed_for_load = DEFAULT_ASSUMED_SPEED_MPS

    # Same "concrete speed if we have one, else the zone's own canonical value" fallback as the
    # load formula below -- computed once here so both load and the display-only field below stay
    # in exact agreement about which number represents this step's intensity.
    intensity_factor: float | None = None
    if speed_for_load is not None and threshold_speed_mps is not None:
        intensity_factor = speed_for_load / threshold_speed_mps
    elif zone is not None:
        intensity_factor = _CANONICAL_ZONE_IF[zone - 1]
    elif step.intensity == "rest":
        intensity_factor = 0.0

    load: float | None = None
    if zone is not None and threshold_pace_sec_per_km is not None:
        if speed_for_load is not None:
            load = compute_running_tss(duration_s, speed_for_load, threshold_pace_sec_per_km)
        else:
            # A zone was determined without a concrete speed (an HR-zone/bpm-bucketed step) --
            # fall back to that zone's own canonical intensity factor instead (same value now
            # held in `intensity_factor` above, for this exact branch).
            load = (duration_s / 3600.0) * _CANONICAL_ZONE_IF[zone - 1] ** 2 * 100.0
    elif step.intensity == "rest":
        load = 0.0

    return StepEstimate(
        duration_s=duration_s,
        distance_m=distance_m,
        zone=zone,
        load=load,
        intensity_factor=intensity_factor,
    )


def estimate_workout(
    steps: list[ParsedStep],
    *,
    threshold_pace_sec_per_km: float | None,
    threshold_hr_bpm: float | None,
    max_hr_bpm: float | None,
    resting_hr_bpm: float | None,
) -> WorkoutEstimate:
    """Expands repeat groups (`workout_syntax.py::expand_repeat_groups`) first, so `segments`
    holds one entry per actually-executed rep -- a 4x repeat of 2 children becomes 8 segments in
    order, not 2 segments carrying a multiplier, matching how the load bar should visually read.
    `load` is `None` only when `threshold_pace_sec_per_km` itself isn't configured -- every other
    gap (a step with no target, an incomplete HR-zone config) degrades to a documented default
    rather than making the whole workout's load unknowable."""
    hr_zone_boundaries = compute_hr_zone_boundaries(max_hr_bpm, threshold_hr_bpm, resting_hr_bpm)
    segments = [
        estimate_step(
            s,
            threshold_pace_sec_per_km=threshold_pace_sec_per_km,
            threshold_hr_bpm=threshold_hr_bpm,
            hr_zone_boundaries=hr_zone_boundaries,
        )
        for s in expand_repeat_groups(steps)
    ]
    duration_s = sum(s.duration_s for s in segments)
    distance_m = sum(s.distance_m for s in segments)
    load = (
        sum(s.load for s in segments if s.load is not None)
        if threshold_pace_sec_per_km is not None
        else None
    )
    return WorkoutEstimate(
        duration_s=duration_s, distance_m=distance_m, load=load, segments=segments
    )
