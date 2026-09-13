import pytest

from perseverer.vdot import (
    AEROBIC_THRESHOLD_VO2MAX_FRACTION,
    RACE_DISTANCES_M,
    THRESHOLD_VO2MAX_FRACTION,
    compute_gap_factor,
    compute_threshold_pace_s_per_km,
    compute_vdot,
    predict_race_time_s,
)


class TestComputeVdot:
    def test_returns_none_for_non_positive_or_missing_inputs(self) -> None:
        assert compute_vdot(0, 1800) is None
        assert compute_vdot(5000, 0) is None
        assert compute_vdot(None, 1800) is None
        assert compute_vdot(5000, None) is None

    def test_returns_none_for_a_duration_too_short_for_the_aerobic_model(self) -> None:
        # 1000m in 150s (2:30/km) pushes %VO2max above 1.0 -- the model's own domain limit (see
        # module docstring: the crossing point is around 11 minutes), not an arbitrary cutoff.
        assert compute_vdot(1000, 150) is None

    def test_matches_independently_hand_worked_values(self) -> None:
        # Cross-checked by independently re-deriving the published VO2/%VO2max equations in the
        # test itself (see the conversation this was built from) -- not just a snapshot of
        # whatever the implementation currently returns.
        easy = compute_vdot(4020, 1570)  # ~6:33/km for 26:10
        assert easy is not None
        assert easy == pytest.approx(27.58, abs=0.01)

        hard = compute_vdot(4000, 1120)  # 4:40/km for 4km
        assert hard is not None
        assert hard == pytest.approx(40.98, abs=0.01)

    def test_a_slower_pace_over_the_same_duration_scores_lower(self) -> None:
        fast = compute_vdot(5000, 1500)
        slow = compute_vdot(4000, 1500)
        assert fast is not None
        assert slow is not None
        assert fast > slow

    def test_gap_factor_below_one_speeds_up_the_effective_pace_and_raises_vdot(self) -> None:
        # factor < 1 means the course was net harder than flat (uphill-heavy) -- the same
        # distance/time should score as a *better* equivalent-flat performance.
        unadjusted = compute_vdot(5000, 1800, gap_factor=1.0)
        uphill_adjusted = compute_vdot(5000, 1800, gap_factor=0.9)
        assert unadjusted is not None
        assert uphill_adjusted is not None
        assert uphill_adjusted > unadjusted

    def test_returns_none_for_a_non_positive_gap_factor(self) -> None:
        assert compute_vdot(5000, 1800, gap_factor=0) is None
        assert compute_vdot(5000, 1800, gap_factor=-1) is None


class TestComputeGapFactor:
    def test_returns_none_for_too_few_points(self) -> None:
        assert compute_gap_factor([0.0], [100.0]) is None
        assert compute_gap_factor([], []) is None

    def test_returns_none_for_mismatched_lengths(self) -> None:
        assert compute_gap_factor([0.0, 100.0], [100.0]) is None

    def test_flat_course_has_a_factor_of_one(self) -> None:
        distances = [float(i * 100) for i in range(11)]
        altitudes = [100.0] * 11
        factor = compute_gap_factor(distances, altitudes)
        assert factor is not None
        assert factor == 1.0

    def test_uniform_uphill_grade_matches_the_minetti_polynomial(self) -> None:
        # 5% grade throughout (5m climb per 100m segment, ten segments) -- independently computed
        # from the same published Minetti coefficients frontend/src/gap.ts also ports, not by
        # calling back into the module under test.
        def cost(i: float) -> float:
            return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6

        expected = 3.6 / cost(0.05)
        distances = [float(i * 100) for i in range(11)]
        altitudes = [float(i * 5) for i in range(11)]
        factor = compute_gap_factor(distances, altitudes)
        assert factor is not None
        assert abs(factor - expected) < 1e-9

    def test_uphill_gives_a_factor_below_one_downhill_above_one(self) -> None:
        distances = [float(i * 100) for i in range(11)]
        uphill = compute_gap_factor(distances, [float(i * 5) for i in range(11)])
        downhill = compute_gap_factor(distances, [float(-i * 5) for i in range(11)])
        assert uphill is not None
        assert downhill is not None
        assert uphill < 1.0
        assert downhill > 1.0

    def test_a_device_pause_zero_distance_large_gap_does_not_corrupt_the_average(self) -> None:
        # A stationary pause: distance stays flat for a stretch (altitude may drift slightly from
        # GPS noise) then resumes -- since this is purely distance-weighted, the paused stretch
        # contributes ~0 weight and shouldn't move the result off the flat-course value.
        distances = [0.0, 100.0, 200.0, 200.0, 200.0, 300.0, 400.0]
        altitudes = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        factor = compute_gap_factor(distances, altitudes)
        assert factor is not None
        assert factor == 1.0

    def test_ignores_segments_with_missing_altitude(self) -> None:
        distances = [0.0, 100.0, 200.0]
        altitudes = [100.0, None, 100.0]
        # Both segments touch the None altitude point, so nothing usable remains.
        assert compute_gap_factor(distances, altitudes) is None


class TestComputeThresholdPaceSPerKm:
    def test_returns_none_for_non_positive_or_missing_vdot(self) -> None:
        assert compute_threshold_pace_s_per_km(0) is None
        assert compute_threshold_pace_s_per_km(-5) is None
        assert compute_threshold_pace_s_per_km(None) is None

    def test_matches_independently_hand_worked_value_at_vdot_50(self) -> None:
        # Independently re-derived from the quadratic in the module docstring:
        # 0.000104*v^2 + 0.182258*v - (4.60 + 0.88*50) = 0 -> v ~= 235.11 m/min -> pace ~= 4:15/km
        # (consistent with publicly known Daniels VDOT=50 Threshold-pace tables).
        pace = compute_threshold_pace_s_per_km(50)
        assert pace is not None
        assert pace == pytest.approx(255.1, abs=0.5)  # ~4:15/km in seconds

    def test_higher_vdot_gives_a_faster_threshold_pace(self) -> None:
        slow = compute_threshold_pace_s_per_km(40)
        fast = compute_threshold_pace_s_per_km(60)
        assert slow is not None
        assert fast is not None
        assert fast < slow

    def test_aerobic_fraction_matches_independently_hand_worked_value_at_vdot_50(self) -> None:
        # Independently re-derived from the same quadratic at AEROBIC_THRESHOLD_VO2MAX_FRACTION:
        # 0.000104*v^2 + 0.182258*v - (4.60 + 0.73*50) = 0 -> v ~= 202.18 m/min -> pace ~= 4:57/km.
        pace = compute_threshold_pace_s_per_km(50, fraction=AEROBIC_THRESHOLD_VO2MAX_FRACTION)
        assert pace is not None
        assert pace == pytest.approx(296.8, abs=0.5)  # ~4:57/km in seconds

    def test_aerobic_threshold_pace_is_always_slower_than_anaerobic_at_the_same_vdot(
        self,
    ) -> None:
        # Physiologically required ordering: the aerobic threshold sits below the anaerobic one
        # on the same intensity continuum, so its pace must be slower (a larger seconds/km).
        for vdot in (35, 45, 55, 65):
            anaerobic = compute_threshold_pace_s_per_km(vdot)
            aerobic = compute_threshold_pace_s_per_km(
                vdot, fraction=AEROBIC_THRESHOLD_VO2MAX_FRACTION
            )
            assert anaerobic is not None
            assert aerobic is not None
            assert aerobic > anaerobic

    def test_default_fraction_is_unchanged_from_before_parameterization(self) -> None:
        assert compute_threshold_pace_s_per_km(50) == compute_threshold_pace_s_per_km(
            50, fraction=THRESHOLD_VO2MAX_FRACTION
        )


class TestPredictRaceTimeS:
    def test_returns_none_for_non_positive_or_missing_vdot(self) -> None:
        assert predict_race_time_s(RACE_DISTANCES_M["5k"], 0) is None
        assert predict_race_time_s(RACE_DISTANCES_M["5k"], -1) is None
        assert predict_race_time_s(RACE_DISTANCES_M["5k"], None) is None

    def test_returns_none_for_an_unknown_distance(self) -> None:
        assert predict_race_time_s(1234.0, 50) is None

    @pytest.mark.parametrize("vdot", [35, 45, 50, 55, 65])
    @pytest.mark.parametrize("label", list(RACE_DISTANCES_M))
    def test_round_trips_through_compute_vdot(self, label: str, vdot: float) -> None:
        # The correct way to validate a numeric inversion of an already-verified formula: feed
        # the predicted time back into the original, already-verified compute_vdot and confirm
        # it reproduces the target VDOT, rather than hand-deriving a second set of reference
        # race times.
        distance_m = RACE_DISTANCES_M[label]
        predicted_s = predict_race_time_s(distance_m, vdot)
        assert predicted_s is not None
        roundtrip_vdot = compute_vdot(distance_m, predicted_s)
        assert roundtrip_vdot is not None
        # The 1-second bisection tolerance (_BISECTION_TOLERANCE_S) has proportionally more VDOT
        # impact on a short/fast race than a long/slow one -- 0.05 stays tight (~0.1% of a
        # typical VDOT) while accommodating that.
        assert roundtrip_vdot == pytest.approx(vdot, abs=0.05)

    def test_higher_vdot_gives_a_faster_predicted_time(self) -> None:
        slow = predict_race_time_s(RACE_DISTANCES_M["10k"], 40)
        fast = predict_race_time_s(RACE_DISTANCES_M["10k"], 60)
        assert slow is not None
        assert fast is not None
        assert fast < slow

    def test_longer_distances_take_longer_at_the_same_vdot(self) -> None:
        times = [predict_race_time_s(m, 50) for m in RACE_DISTANCES_M.values()]
        assert all(t is not None for t in times)
        non_none_times = [t for t in times if t is not None]
        # 5k < 10k < half < marathon, in RACE_DISTANCES_M's own declared order.
        assert non_none_times == sorted(non_none_times)

    def test_returns_none_for_a_vdot_outside_the_search_bounds(self) -> None:
        # Effectively unachievable at any real distance, in either direction.
        assert predict_race_time_s(RACE_DISTANCES_M["marathon"], 1.0) is None
        assert predict_race_time_s(RACE_DISTANCES_M["5k"], 10000.0) is None
