"""Tests for GET /activities/{id}/context (Milestone E of Phase 6.1 -- see
docs/adr/0010-phase-6.1-frontend-design.md's plan). percentile_rank and the distance-band
comparison pool are cross-checked independently below (not just re-asserting whatever the
endpoint itself computes), matching the manual cross-check already run against the real
archive during development.
"""

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.conftest import seed_activity


def test_context_404_for_unknown_activity(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/activities/doesnotexist/context", headers=auth_headers)
    assert r.status_code == 404


def test_no_percentile_with_no_comparable_activities(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A first-of-its-kind effort has nothing to rank against -- percentile_rank must be null,
    not 0 or 100 (CLAUDE.md's "never invent a plausible-looking number")."""
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running", distance_m=5000.0, duration_s=1500.0)

    r = client.get("/api/v1/activities/a1/context", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["percentile_rank"] is None
    assert body["comparable_count"] == 0
    # `recent` legitimately includes the activity itself -- it's part of its own 90-day
    # window, and a sparkline needs "this one" plotted among its contemporaries.
    assert [r["id"] for r in body["recent"]] == ["a1"]


def test_percentile_excludes_self_from_the_comparison_pool(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """With exactly one other comparable activity, comparable_count must be 1 (not 2) -- the
    target's own trivially-in-band distance must not count as a comparison against itself."""
    with engine.connect() as conn:
        seed_activity(
            conn, activity_id="a1", sport="running", distance_m=5000.0,
            duration_s=1500.0, moving_duration_s=1500.0,
        )
        seed_activity(
            conn, activity_id="a2", sport="running", local_date="2025-06-02",
            distance_m=5000.0, duration_s=1500.0, moving_duration_s=1500.0,
        )

    r = client.get("/api/v1/activities/a1/context", headers=auth_headers)
    body = r.json()
    assert body["comparable_count"] == 1
    # Identical pace -> "faster than or equal to" -> 100%.
    assert body["percentile_rank"] == 100.0


def test_percentile_rank_matches_independent_calculation(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """4 comparable 5Ks at distinct paces; the target beats 2 of them (slower-or-equal) ->
    2/4 = 50%. Computed by hand here rather than re-deriving the endpoint's own formula."""
    with engine.connect() as conn:
        # Target: 5000m in 1500s moving -> pace 0.3 s/m.
        seed_activity(
            conn, activity_id="target", sport="running", distance_m=5000.0,
            duration_s=1500.0, moving_duration_s=1500.0,
        )
        # Faster than target (lower pace): excluded from "slower or equal" count.
        seed_activity(
            conn, activity_id="faster1", sport="running", local_date="2025-06-02",
            distance_m=5000.0, duration_s=1200.0, moving_duration_s=1200.0,
        )
        seed_activity(
            conn, activity_id="faster2", sport="running", local_date="2025-06-03",
            distance_m=5000.0, duration_s=1300.0, moving_duration_s=1300.0,
        )
        # Slower than target: included in "slower or equal" count.
        seed_activity(
            conn, activity_id="slower1", sport="running", local_date="2025-06-04",
            distance_m=5000.0, duration_s=1600.0, moving_duration_s=1600.0,
        )
        seed_activity(
            conn, activity_id="slower2", sport="running", local_date="2025-06-05",
            distance_m=5000.0, duration_s=1800.0, moving_duration_s=1800.0,
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert body["comparable_count"] == 4
    assert body["percentile_rank"] == 50.0


def test_distance_band_excludes_activities_outside_15_percent(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A 10K is not a comparable effort for a 5K's percentile -- only activities within +/-15%
    of the target's own distance count."""
    with engine.connect() as conn:
        seed_activity(
            conn, activity_id="target", sport="running", distance_m=5000.0,
            duration_s=1500.0, moving_duration_s=1500.0,
        )
        # Within band: 5000 * 1.1 = 5500.
        seed_activity(
            conn, activity_id="in_band", sport="running", local_date="2025-06-02",
            distance_m=5500.0, duration_s=1600.0, moving_duration_s=1600.0,
        )
        # Outside band: a 10K.
        seed_activity(
            conn, activity_id="out_of_band", sport="running", local_date="2025-06-03",
            distance_m=10000.0, duration_s=3000.0, moving_duration_s=3000.0,
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert body["comparable_count"] == 1


def test_distance_band_is_scoped_to_the_same_sport(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A cycling activity at the same distance must not count as a comparable running effort."""
    with engine.connect() as conn:
        seed_activity(
            conn, activity_id="target", sport="running", distance_m=5000.0,
            duration_s=1500.0, moving_duration_s=1500.0,
        )
        seed_activity(
            conn, activity_id="bike", sport="cycling", local_date="2025-06-02",
            distance_m=5000.0, duration_s=900.0, moving_duration_s=900.0,
        )

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    assert body["comparable_count"] == 0
    assert body["percentile_rank"] is None


def test_recent_window_is_90_days_up_to_the_activity_own_date_not_today(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """Viewing an old activity should show its own contemporaries -- the 90-day window ends at
    that activity's local_date, not at "today"."""
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running", local_date="2025-06-01")
        # Within 90 days before the target's own date.
        seed_activity(conn, activity_id="within", sport="running", local_date="2025-04-01")
        # More than 90 days before the target's own date -- excluded.
        seed_activity(conn, activity_id="too_old", sport="running", local_date="2024-12-01")
        # After the target's own date -- excluded (this is "recent as of that day", not a
        # window centered on it).
        seed_activity(conn, activity_id="after", sport="running", local_date="2025-06-15")

    r = client.get("/api/v1/activities/target/context", headers=auth_headers)
    body = r.json()
    recent_ids = {r["id"] for r in body["recent"]}
    assert recent_ids == {"target", "within"}


def test_recent_is_ordered_chronologically(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="third", sport="running", local_date="2025-06-03")
        seed_activity(conn, activity_id="first", sport="running", local_date="2025-06-01")
        seed_activity(conn, activity_id="second", sport="running", local_date="2025-06-02")

    r = client.get("/api/v1/activities/third/context", headers=auth_headers)
    body = r.json()
    assert [r["id"] for r in body["recent"]] == ["first", "second", "third"]
