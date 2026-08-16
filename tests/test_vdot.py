import pytest

from sporthealth.vdot import compute_gap_factor, compute_vdot


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
