"""Tests for planned_workout_stats.py: the zone-bucketing + Coggan-style load estimate for a
scheduled running workout. workout_syntax.py's own parsing/repeat-expansion is exhaustively
covered by tests/test_workout_syntax.py; these tests exercise only the new estimate layer on
top of it (zone assignment, load formula, repeat-group segment expansion, graceful degradation
with no threshold configured).
"""

from __future__ import annotations

from perseverer.planned_workout_stats import WorkoutEstimate, estimate_step, estimate_workout
from perseverer.running_load import compute_running_tss
from perseverer.workout_syntax import parse_workout_syntax

THRESHOLD_PACE_S_PER_KM = 270.0  # 4:30/km, threshold speed = 1000/270 mps
THRESHOLD_HR_BPM = 165.0
MAX_HR_BPM = 185.0
RESTING_HR_BPM = 50.0


def _estimate(
    text: str,
    *,
    threshold_pace_sec_per_km: float | None = THRESHOLD_PACE_S_PER_KM,
    threshold_hr_bpm: float | None = THRESHOLD_HR_BPM,
    max_hr_bpm: float | None = MAX_HR_BPM,
    resting_hr_bpm: float | None = RESTING_HR_BPM,
) -> WorkoutEstimate:
    parsed = parse_workout_syntax(text)
    assert parsed.errors == []
    return estimate_workout(
        parsed.steps,
        threshold_pace_sec_per_km=threshold_pace_sec_per_km,
        threshold_hr_bpm=threshold_hr_bpm,
        max_hr_bpm=max_hr_bpm,
        resting_hr_bpm=resting_hr_bpm,
    )


class TestPaceTargetedStep:
    def test_zone_and_load_from_a_fast_pace_target(self) -> None:
        # 3:30-3:40/km is well faster than the 4:30/km threshold -- zone 5 (repetition).
        we = _estimate("30s 3:30-3:40/km Pace")
        assert len(we.segments) == 1
        seg = we.segments[0]
        assert seg.zone == 5
        target_speed_mps = (1000 / 210 + 1000 / 220) / 2
        expected_load = compute_running_tss(30.0, target_speed_mps, THRESHOLD_PACE_S_PER_KM)
        assert seg.load is not None and expected_load is not None
        assert seg.load == expected_load
        assert we.load == expected_load

    def test_easy_pace_target_lands_in_zone_1(self) -> None:
        # 6:00/km is much slower than the 4:30/km threshold -- easy zone.
        we = _estimate("10m 6:00/km Pace")
        assert we.segments[0].zone == 1

    def test_distance_step_gets_an_estimated_duration_from_its_own_pace_target(self) -> None:
        we = _estimate("2km 4:30/km Pace")
        seg = we.segments[0]
        assert seg.distance_m == 2000.0
        assert seg.duration_s == 2000.0 / (1000.0 / 270.0)

    def test_two_paces_in_the_same_zone_still_get_different_intensity_factors(self) -> None:
        # A real bug report: 5:10-5:30/km and 4:50-5:15/km both land in zone 2 against a fast
        # (4:41/km) threshold pace, so `zone` alone can't tell the frontend's load bar these are
        # different efforts -- `intensity_factor` (continuous) must still differ between them.
        we = _estimate(
            "3m 5:10-5:30/km Pace\n20s 4:50-5:15/km Pace",
            threshold_pace_sec_per_km=281.0,
        )
        slower, faster = we.segments
        assert slower.zone == 2
        assert faster.zone == 2
        assert slower.intensity_factor is not None and faster.intensity_factor is not None
        assert faster.intensity_factor > slower.intensity_factor


class TestIntensityFallback:
    def test_warmup_lands_in_zone_1_with_no_target_at_all(self) -> None:
        we = _estimate("Warmup 10m")
        seg = we.segments[0]
        assert seg.zone == 1
        assert seg.duration_s == 600.0
        assert seg.load is not None and seg.load > 0

    def test_rest_is_excluded_from_load_but_still_has_duration(self) -> None:
        we = _estimate("rest 2m")
        seg = we.segments[0]
        assert seg.zone is None
        assert seg.load == 0.0
        assert seg.intensity_factor == 0.0
        assert seg.duration_s == 120.0

    def test_untargeted_active_step_falls_back_to_default_assumed_speed(self) -> None:
        we = _estimate("5m")
        seg = we.segments[0]
        assert seg.zone is not None
        assert seg.load is not None and seg.load > 0


class TestHeartRateTargetedStep:
    def test_explicit_hr_zone_maps_directly_to_the_same_zone_number(self) -> None:
        we = _estimate("5m Z4 HR")
        assert we.segments[0].zone == 4

    def test_absolute_bpm_target_is_bucketed_via_hr_zone_boundaries(self) -> None:
        # threshold_hr=165 puts a 170bpm target above threshold -- zone 4 or 5, not zone 1.
        we = _estimate("5m 168-172 HR")
        assert we.segments[0].zone is not None and we.segments[0].zone >= 4

    def test_absolute_bpm_target_without_zone_config_falls_through_with_no_zone(self) -> None:
        we = _estimate(
            "5m 168-172 HR",
            threshold_hr_bpm=None,
            max_hr_bpm=None,
            resting_hr_bpm=None,
        )
        assert we.segments[0].zone is None
        assert we.segments[0].load is None
        assert we.segments[0].intensity_factor is None


class TestRepeatGroupExpansion:
    def test_a_4x_block_produces_four_repeats_worth_of_segments_not_one(self) -> None:
        we = _estimate(
            """Warmup 5m

4x
30s 3:30/km Pace
recovery 30s

Cooldown 5m"""
        )
        # warmup + 4*(interval, recovery) + cooldown = 1 + 8 + 1
        assert len(we.segments) == 10
        interval_zones = [s.zone for s in we.segments[1:9:2]]
        assert interval_zones == [5, 5, 5, 5]
        recovery_zones = [s.zone for s in we.segments[2:9:2]]
        assert recovery_zones == [1, 1, 1, 1]

    def test_total_duration_and_distance_match_manual_sum_across_the_repeats(self) -> None:
        we = _estimate(
            """4x
30s 3:30/km Pace
recovery 30s"""
        )
        assert we.duration_s == 4 * (30.0 + 30.0)
        assert we.distance_m == sum(s.distance_m for s in we.segments)


class TestNoThresholdConfigured:
    def test_load_is_none_for_the_whole_workout_without_a_threshold_pace(self) -> None:
        we = _estimate("Warmup 10m\n2km 4:30/km Pace", threshold_pace_sec_per_km=None)
        assert we.load is None
        # Duration/distance are still real, independent of any threshold.
        assert we.duration_s > 0
        assert we.distance_m > 0
        # A pace-targeted step's own zone still needs a threshold speed to compare against.
        assert we.segments[1].zone is None

    def test_intensity_and_explicit_hr_zone_steps_still_get_a_zone_without_threshold_pace(
        self,
    ) -> None:
        # An explicit HR zone or an intensity word don't depend on threshold *pace* at all.
        we = _estimate("Warmup 10m\n5m Z3 HR", threshold_pace_sec_per_km=None)
        assert we.segments[0].zone == 1
        assert we.segments[1].zone == 3
        # But no concrete load number without a pace threshold to scale against.
        assert we.segments[0].load is None
        assert we.segments[1].load is None


def test_estimate_step_matches_estimate_workout_for_a_single_step() -> None:
    parsed = parse_workout_syntax("30s 3:30/km Pace")
    step = parsed.steps[0]
    direct = estimate_step(
        step,
        threshold_pace_sec_per_km=THRESHOLD_PACE_S_PER_KM,
        threshold_hr_bpm=THRESHOLD_HR_BPM,
        hr_zone_boundaries=None,
    )
    via_workout = estimate_workout(
        [step],
        threshold_pace_sec_per_km=THRESHOLD_PACE_S_PER_KM,
        threshold_hr_bpm=THRESHOLD_HR_BPM,
        max_hr_bpm=MAX_HR_BPM,
        resting_hr_bpm=RESTING_HR_BPM,
    ).segments[0]
    assert direct.zone == via_workout.zone
    assert direct.load == via_workout.load
