"""Property-based tests for the merge engine's threshold boundaries — the spec calls for
Hypothesis tests here specifically, since the matching rule's correctness is really about
its behavior across the threshold boundary, not any single example.
"""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from sporthealth.merge.engine import (
    ActivityCandidate,
    is_same_activity,
    sport_family,
)

BASE_TIME = datetime(2024, 1, 1, tzinfo=UTC)


@given(
    start_delta_s=st.floats(min_value=-179, max_value=179),
    duration_s=st.floats(min_value=60, max_value=7200),
)
def test_matches_within_default_thresholds(start_delta_s: float, duration_s: float) -> None:
    a = ActivityCandidate(BASE_TIME, duration_s, "running")
    b = ActivityCandidate(BASE_TIME + timedelta(seconds=start_delta_s), duration_s, "running")
    assert is_same_activity(a, b).is_match


@given(start_delta_s=st.floats(min_value=181, max_value=10_000))
def test_never_matches_beyond_start_time_threshold(start_delta_s: float) -> None:
    a = ActivityCandidate(BASE_TIME, 1800.0, "running")
    b = ActivityCandidate(BASE_TIME + timedelta(seconds=start_delta_s), 1800.0, "running")
    assert not is_same_activity(a, b).is_match


@given(duration_delta_s=st.floats(min_value=0, max_value=59))
def test_short_activities_match_within_flat_60s_floor(duration_delta_s: float) -> None:
    # For short activities the 60s absolute floor dominates the 5% relative threshold.
    a = ActivityCandidate(BASE_TIME, 100.0, "running")
    b = ActivityCandidate(BASE_TIME, 100.0 + duration_delta_s, "running")
    assert is_same_activity(a, b).is_match


@given(duration_delta_pct=st.floats(min_value=0.06, max_value=0.5))
def test_long_activities_never_match_beyond_5pct_duration_delta(duration_delta_pct: float) -> None:
    # For long activities the 5% relative threshold dominates the 60s absolute floor.
    duration = 10_000.0
    a = ActivityCandidate(BASE_TIME, duration, "running")
    b = ActivityCandidate(BASE_TIME, duration * (1 + duration_delta_pct), "running")
    assert not is_same_activity(a, b).is_match


def test_different_sport_families_never_match() -> None:
    a = ActivityCandidate(BASE_TIME, 1800.0, "running")
    b = ActivityCandidate(BASE_TIME, 1800.0, "cycling")
    assert not is_same_activity(a, b).is_match


def test_missing_duration_does_not_block_an_otherwise_good_match() -> None:
    a = ActivityCandidate(BASE_TIME, None, "running")
    b = ActivityCandidate(BASE_TIME, 1800.0, "running")
    assert is_same_activity(a, b).is_match


def test_sport_family_maps_known_aliases() -> None:
    assert sport_family("trail_running") == "run"
    assert sport_family("road_biking") == "ride"
    assert sport_family("RUNNING") == "run"  # case-insensitive


def test_sport_family_falls_back_to_itself_for_unknown_sports() -> None:
    assert sport_family("totally_unknown_sport") == "totally_unknown_sport"


def test_decision_reasons_and_inputs_are_populated_for_auditing() -> None:
    a = ActivityCandidate(BASE_TIME, 1800.0, "running")
    b = ActivityCandidate(BASE_TIME, 1800.0, "running")
    decision = is_same_activity(a, b)
    assert decision.reasons
    assert decision.inputs["a_sport"] == "running"


def test_matches_when_one_side_is_naive_and_the_other_aware() -> None:
    """Regression test: SQLite/SQLAlchemy don't round-trip tzinfo (see db/schema.py), so a
    freshly-parsed (aware) activity is routinely compared against one read back from the
    database (naive). This used to raise `TypeError: can't subtract offset-naive and
    offset-aware datetimes` on real data — see docs/adr/0002-phase-1-schema-and-ingestion.md.
    """
    aware = ActivityCandidate(BASE_TIME, 1800.0, "running")
    naive = ActivityCandidate(BASE_TIME.replace(tzinfo=None), 1800.0, "running")
    assert is_same_activity(aware, naive).is_match
    assert is_same_activity(naive, aware).is_match
