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


def compute_hr_zone_boundaries(
    max_hr_bpm: float | None,
    threshold_hr_bpm: float | None,
    resting_hr_bpm: float | None,
) -> tuple[float, float, float, float] | None:
    """Returns (zone1_high, zone2_high, zone3_high, zone4_high) bpm -- the four ceilings that
    define five zones (Z5 is open-ended above zone4_high). None if any of the three reference
    values is missing; there's no sensible partial computation for this blended formula, unlike
    e.g. the workout overlay's independently-optional per-step fields.

    The two formulas (HRR for zones 1-2, %threshold for zones 3-4) are independent -- confirmed
    against real, unremarkable inputs (max 185, threshold 160, resting 45) that the raw HRR-based
    zone2 ceiling can land *above* the raw threshold-based zone3 ceiling, which would make zone 3
    unreachable (a value between them would already have matched zone 2 first). Each boundary is
    therefore ratcheted to be at least the previous one, the same "each zone starts where the
    last one ended" invariant the device-reported zones already have.
    """
    if max_hr_bpm is None or threshold_hr_bpm is None or resting_hr_bpm is None:
        return None
    hrr = max_hr_bpm - resting_hr_bpm
    zone1_high = resting_hr_bpm + Z1_HRR_FRACTION * hrr
    zone2_high = max(resting_hr_bpm + Z2_HRR_FRACTION * hrr, zone1_high)
    zone3_high = max(threshold_hr_bpm * Z3_THRESHOLD_FRACTION, zone2_high)
    zone4_high = max(threshold_hr_bpm * Z4_THRESHOLD_FRACTION, zone3_high)
    return (zone1_high, zone2_high, zone3_high, zone4_high)
