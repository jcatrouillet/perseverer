"""Tests for hr_zones.py::compute_hr_zone_boundaries -- the blended HRR/threshold formula
(zones 1-2 from heart rate reserve, zones 3-4 from lactate threshold HR)."""

from __future__ import annotations

from perseverer.hr_zones import compute_hr_zone_boundaries


def test_returns_none_when_any_reference_value_is_missing() -> None:
    assert compute_hr_zone_boundaries(None, 165.0, 50.0) is None
    assert compute_hr_zone_boundaries(190.0, None, 50.0) is None
    assert compute_hr_zone_boundaries(190.0, 165.0, None) is None
    assert compute_hr_zone_boundaries(None, None, None) is None


def test_computes_the_blended_hrr_and_threshold_boundaries() -> None:
    # HRR = 190 - 50 = 140.
    # Z1 = 50 + 0.68*140 = 145.2; Z2 = 50 + 0.83*140 = 166.2.
    # Raw Z3 = 165*0.94 = 155.1 -- below zone2 (166.2), so the ratchet holds it at 166.2.
    # Raw Z4 = 165*1.05 = 173.25 -- already above the (ratcheted) zone3, so it's unaffected.
    result = compute_hr_zone_boundaries(
        max_hr_bpm=190.0, threshold_hr_bpm=165.0, resting_hr_bpm=50.0
    )
    assert result is not None
    zone1, zone2, zone3, zone4 = result
    assert zone1 == 145.2
    assert zone2 == 166.2
    assert zone3 == 166.2
    assert round(zone4, 2) == 173.25


def test_boundaries_are_never_decreasing_even_across_many_realistic_inputs() -> None:
    # A sweep of plausible (max, threshold, resting) triples -- confirmed for real that the raw
    # HRR-based zone2 ceiling can land *above* the raw threshold-based zone3 ceiling for some
    # combinations (e.g. max=185, threshold=160, resting=45 raw values give zone2=161.2 >
    # zone3=150.4), which would make zone 3 unreachable. The ratchet must hold for all of them.
    for max_hr in (170.0, 185.0, 195.0, 205.0):
        for threshold_hr in (140.0, 155.0, 160.0, 175.0):
            for resting_hr in (40.0, 45.0, 60.0):
                if threshold_hr > max_hr or resting_hr >= max_hr:
                    continue
                result = compute_hr_zone_boundaries(max_hr, threshold_hr, resting_hr)
                assert result is not None
                assert result[0] <= result[1] <= result[2] <= result[3]


def test_ratchets_a_lower_hrr_boundary_up_to_match_a_higher_earlier_one() -> None:
    # Raw zone2 (HRR) would be 45 + 0.83*140 = 161.2; raw zone3 (threshold) would be
    # 160*0.94 = 150.4 -- without the ratchet, zone3 < zone2, leaving zone 3 empty.
    result = compute_hr_zone_boundaries(
        max_hr_bpm=185.0, threshold_hr_bpm=160.0, resting_hr_bpm=45.0
    )
    assert result is not None
    assert result[2] == result[1]  # zone3 ratcheted up rather than left lower than zone2
