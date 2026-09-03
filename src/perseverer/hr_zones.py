"""Pure computation of an athlete's 5 HR training zone boundaries from three reference points:
max HR, lactate threshold HR, and resting HR (for the Karvonen/heart-rate-reserve calculation).
Blended model, chosen by the athlete over a %max-HR-only or %threshold-only scheme: the lower
zones (recovery/aerobic) are defined relative to heart rate reserve, which correlates with
perceived effort better than %max HR does at low intensity; the upper zones (tempo/threshold/
anaerobic) are defined relative to lactate threshold HR, the more physiologically meaningful
anchor near and above threshold. Matches the hybrid approach used by several run-coaching
frameworks (e.g. 80/20 Endurance-style zone models).
"""

from __future__ import annotations

# Z1 ceiling: heart rate reserve fraction above resting HR (Karvonen: resting + fraction *
# (max - resting)).
Z1_HRR_FRACTION = 0.68
# Z2 ceiling: heart rate reserve fraction above resting HR.
Z2_HRR_FRACTION = 0.83
# Z3 ceiling: fraction of lactate threshold HR.
Z3_THRESHOLD_FRACTION = 0.94
# Z4 ceiling: fraction of lactate threshold HR (>100% -- Z4 straddles threshold itself).
Z4_THRESHOLD_FRACTION = 1.05

# Below this width, a zone isn't just numerically imperfect, it's practically meaningless -- a
# 1 bpm band isn't somewhere a heart rate can meaningfully sit during training, it's a rounding
# artifact wearing a zone's clothes. Used two ways below: as the trigger for "these two raw
# ceilings clash badly enough to intervene", and as the guaranteed minimum width once we do.
# 5 bpm is a judgment call, not a physiological constant -- adjust it if it doesn't feel right.
_MIN_MEANINGFUL_ZONE_WIDTH_BPM = 5
# The hard floor every boundary must still clear even after the clash handling below (belt and
# suspenders for a genuinely extreme input where even the split leaves no room) -- this alone was
# a confirmed real bug on its own the first time around: forcing only *this* much room between
# zone2 and zone3 technically satisfies "not zero-width" while still producing a zone nobody
# could ever meaningfully land a heartbeat in. It's the last-resort floor now, not the fix.
_MIN_ZONE_WIDTH_BPM = 1


def compute_hr_zone_boundaries(
    max_hr_bpm: float | None,
    threshold_hr_bpm: float | None,
    resting_hr_bpm: float | None,
) -> tuple[int, int, int, int] | None:
    """Returns (zone1_high, zone2_high, zone3_high, zone4_high) bpm, rounded to the nearest
    whole beat -- the four ceilings that define five zones (Z5 is open-ended above zone4_high).
    None if any of the three reference values is missing; there's no sensible partial
    computation for this blended formula, unlike e.g. the workout overlay's independently-
    optional per-step fields.

    Rounded here, not left as a raw float for the presentation layer to round: nothing downstream
    (this API response, TimeInZoneChart.tsx's own bucketing of real per-second HR samples, which
    are themselves always whole bpm off any real device) benefits from sub-bpm precision, and a
    value like 161.70000000000002 reaching the frontend unrounded was a confirmed real bug.

    **Zone 2 and Zone 3 share a boundary computed by two independent formulas** (zone2's own
    ceiling: HRR; zone3's own ceiling: %threshold) that can disagree badly for a perfectly
    ordinary athlete -- confirmed against real reported inputs (max 170 / threshold 154 /
    resting 45: threshold sits at ~87% of this athlete's own heart rate reserve, which pushes
    zone3's raw threshold-based ceiling *below* zone2's raw HRR-based one) and reproduced in this
    module's own original test fixtures too, so this isn't a rare edge case. A first attempt
    fixed only the *literal* symptom -- forcing zone3 to land at least 1 bpm above zone2 -- which
    technically satisfies "not zero-width" while still producing a 1 bpm sliver nobody could
    train in. The actual fix: when the two raw ceilings leave less than
    `_MIN_MEANINGFUL_ZONE_WIDTH_BPM` between them, zone2's own HRR-derived ceiling is treated as
    incompatible with this athlete's specific threshold and is pulled down (not zone3 pushed up)
    to the midpoint between zone1's ceiling and zone3's own raw ceiling -- splitting the
    available room so *both* zone2 and zone3 come out with genuinely usable width, rather than
    one of them being squeezed to the ratchet's bare minimum. Zone1 and zone4 never participate
    in this: zone1 has no formula to clash with (it's the first HRR-based zone), and zone4 is
    threshold-based like zone3, so the two stay consistent with each other by construction.
    """
    if max_hr_bpm is None or threshold_hr_bpm is None or resting_hr_bpm is None:
        return None
    hrr = max_hr_bpm - resting_hr_bpm
    zone1_high = round(resting_hr_bpm + Z1_HRR_FRACTION * hrr)
    zone2_high_raw = round(resting_hr_bpm + Z2_HRR_FRACTION * hrr)
    zone3_high_raw = round(threshold_hr_bpm * Z3_THRESHOLD_FRACTION)

    if zone3_high_raw - zone2_high_raw >= _MIN_MEANINGFUL_ZONE_WIDTH_BPM:
        # No clash: both raw ceilings already leave zone3 a real width -- use them as computed.
        zone2_high = max(zone2_high_raw, zone1_high + _MIN_ZONE_WIDTH_BPM)
        zone3_high = zone3_high_raw
    else:
        # Clash: zone2's own HRR value would leave zone3 a sliver (or invert it entirely) --
        # split the room between zone1 and zone3's own raw ceiling instead, so both zones end up
        # with real, comparable width.
        zone2_high = round((zone1_high + zone3_high_raw) / 2)
        zone2_high = max(zone2_high, zone1_high + _MIN_ZONE_WIDTH_BPM)
        zone3_high = max(zone3_high_raw, zone2_high + _MIN_MEANINGFUL_ZONE_WIDTH_BPM)

    zone4_high_raw = round(threshold_hr_bpm * Z4_THRESHOLD_FRACTION)
    zone4_high = max(zone4_high_raw, zone3_high + _MIN_ZONE_WIDTH_BPM)
    return (zone1_high, zone2_high, zone3_high, zone4_high)


def resolve_hr_zone_bpm(
    zone_number: int, boundaries: tuple[int, int, int, int], max_hr_bpm: float
) -> tuple[float, float]:
    """(low, high) bpm for one of the 5 zones `compute_hr_zone_boundaries` defines -- used to
    turn a planned-workout step's `target_hr_zone` (e.g. "Z2 HR", `workout_syntax.py`) into the
    absolute bpm range Garmin's own `heart.rate.zone` target actually wants
    (`adapters/garmin_connect.py::push_planned_workout`); Garmin has no concept of *this app's*
    zone numbering, only a bpm range or one of *its own* configured zone slots, so resolving to
    bpm here (rather than trying to map onto Garmin's own zoneNumber) is what lets this work
    regardless of whether the athlete has ever configured zones on the Garmin side at all.

    Zone 1's floor is 0 (this blended model has no lower reference point below resting HR to
    anchor it to -- Z1 is simply "below zone1_high"). Zone 5 is open-ended in
    `compute_hr_zone_boundaries` itself (see its own docstring); here it's closed at
    `max_hr_bpm` (or `zone4_high + 1`, whichever is higher, so a configured max_hr_bpm lower than
    the computed zone4_high -- an inconsistent but not impossible configuration -- still yields a
    valid, non-empty range) since a push to Garmin needs a real upper bound, not "no limit"."""
    if not 1 <= zone_number <= 5:
        raise ValueError(f"zone_number must be 1-5, got {zone_number}")
    zone1_high, zone2_high, zone3_high, zone4_high = boundaries
    ceilings = (zone1_high, zone2_high, zone3_high, zone4_high, max(max_hr_bpm, zone4_high + 1))
    low = 0.0 if zone_number == 1 else float(ceilings[zone_number - 2] + 1)
    high = float(ceilings[zone_number - 1])
    return (low, high)
