"""Tests for hr_zones.py::compute_hr_zone_boundaries -- the blended HRR/threshold formula
(zones 1-2 from heart rate reserve, zones 3-4 from lactate threshold HR), and the clash-handling
between zone2's HRR-based ceiling and zone3's threshold-based one when they disagree badly for a
given athlete's own numbers (see the module's own docstring for the real reported case)."""

from __future__ import annotations

from perseverer.hr_zones import (
    _MIN_MEANINGFUL_ZONE_WIDTH_BPM,
    compute_hr_zone_boundaries,
)


def test_returns_none_when_any_reference_value_is_missing() -> None:
    assert compute_hr_zone_boundaries(None, 165.0, 50.0) is None
    assert compute_hr_zone_boundaries(190.0, None, 50.0) is None
    assert compute_hr_zone_boundaries(190.0, 165.0, None) is None
    assert compute_hr_zone_boundaries(None, None, None) is None


def test_uses_the_raw_formulas_directly_when_they_dont_clash() -> None:
    # HRR = 210 - 50 = 160. Z1 = 50 + 0.68*160 = 158.8 -> 159; Z2 = 50 + 0.83*160 = 182.8 -> 183.
    # Raw Z3 = 200*0.94 = 188 -- 5 bpm above zone2, right at the meaningful-width threshold, no
    # clash -- both ceilings are used exactly as their own formula computed them.
    result = compute_hr_zone_boundaries(
        max_hr_bpm=210.0, threshold_hr_bpm=200.0, resting_hr_bpm=50.0
    )
    assert result is not None
    zone1, zone2, zone3, zone4 = result
    assert zone1 == 159
    assert zone2 == 183
    assert zone3 == 188  # zone3's own raw value, unmodified
    assert zone4 == 210  # 200*1.05 = 210


def test_boundaries_are_strictly_increasing_across_many_realistic_inputs() -> None:
    # A sweep of plausible (max, threshold, resting) triples, including combinations confirmed
    # for real to make zone2's raw HRR ceiling land at or above zone3's raw threshold ceiling.
    # Every boundary must come out strictly greater than the previous one.
    for max_hr in (170.0, 185.0, 195.0, 205.0):
        for threshold_hr in (140.0, 154.0, 155.0, 160.0, 175.0):
            for resting_hr in (40.0, 45.0, 60.0):
                if threshold_hr > max_hr or resting_hr >= max_hr:
                    continue
                result = compute_hr_zone_boundaries(max_hr, threshold_hr, resting_hr)
                assert result is not None
                assert result[0] < result[1] < result[2] < result[3]


def test_clash_pulls_zone2_down_to_give_both_zones_real_width() -> None:
    # Raw zone2 (HRR) = 45 + 0.83*140 = 161.2 -> 161; raw zone3 (threshold) = 160*0.94 = 150.4
    # -> 150 -- a clash (zone2's raw ceiling is *above* zone3's). Fixing this by forcing zone3
    # up to just above zone2 (a first attempt at this fix) produced a real, reported bug: a
    # technically non-zero but practically useless 1 bpm zone. The actual fix pulls zone2's own
    # ceiling down to the midpoint between zone1 and zone3's raw ceiling instead, so both zones
    # end up with genuinely usable width -- not just zone3 forced above the ratchet's bare
    # minimum.
    result = compute_hr_zone_boundaries(
        max_hr_bpm=185.0, threshold_hr_bpm=160.0, resting_hr_bpm=45.0
    )
    assert result is not None
    zone1, zone2, zone3, zone4 = result
    assert zone1 == 140
    assert zone2 == 145  # pulled down from its own raw 161 -- midpoint of (140, 150)
    assert zone3 == 150  # zone3's own raw ceiling, untouched
    assert zone4 == 168
    assert zone3 - zone2 >= _MIN_MEANINGFUL_ZONE_WIDTH_BPM
    assert zone2 - zone1 >= _MIN_MEANINGFUL_ZONE_WIDTH_BPM


def test_a_real_reported_configuration_gets_genuinely_usable_zones() -> None:
    # The exact (max=170, threshold=154, resting=45) configuration a real user reported: zone2's
    # raw HRR ceiling (148.75) landed above zone3's raw threshold ceiling (144.76). The original
    # bug collapsed zone3 to "148.75 - 148.75" (zero width); a first attempted fix produced
    # "149 - 150" (1 bpm -- still not a real zone). This must now give zone3 real, usable width.
    result = compute_hr_zone_boundaries(
        max_hr_bpm=170.0, threshold_hr_bpm=154.0, resting_hr_bpm=45.0
    )
    assert result is not None
    zone1, zone2, zone3, zone4 = result
    assert zone1 == 130
    assert zone2 == 138
    assert zone3 == 145
    assert zone4 == 162
    assert zone3 - zone2 >= _MIN_MEANINGFUL_ZONE_WIDTH_BPM
    # Every boundary is a whole number -- no more "161.70000000000002".
    assert all(isinstance(b, int) for b in result)


def test_zone4_stays_independent_of_the_zone2_zone3_clash() -> None:
    # zone4 is threshold-based like zone3, so it should be unaffected by whatever happened to
    # zone2 -- confirm it still matches its own raw formula in both a clashing and a
    # non-clashing case.
    clashing = compute_hr_zone_boundaries(185.0, 160.0, 45.0)
    non_clashing = compute_hr_zone_boundaries(210.0, 200.0, 50.0)
    assert clashing is not None and non_clashing is not None
    assert clashing[3] == round(160.0 * 1.05)
    assert non_clashing[3] == round(200.0 * 1.05)
