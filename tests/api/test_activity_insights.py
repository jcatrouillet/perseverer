"""Tests for GET /activities/{id}/insights -- the point-in-time, per-activity view built on
insights/rules_activity.py. The one property every test here ultimately protects is "never look
into the future": an activity's insights must never be able to see, or be influenced by, an
activity dated after it.
"""

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.conftest import seed_activity


def test_insights_404_for_unknown_activity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/activities/doesnotexist/insights", headers=auth_headers)
    assert r.status_code == 404


def test_a_later_faster_run_never_credits_an_earlier_activity(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """The core look-ahead-safety guarantee: an old 10K's insights must not change just because
    a faster 10K was run afterwards."""
    with engine.connect() as conn:
        seed_activity(
            conn, activity_id="earlier", local_date="2025-06-01",
            sport="running", distance_m=10000.0, duration_s=3200.0, moving_duration_s=3200.0,
        )
        seed_activity(
            conn, activity_id="later_and_faster", local_date="2025-06-15",
            sport="running", distance_m=10000.0, duration_s=3000.0, moving_duration_s=3000.0,
        )

    r = client.get("/api/v1/activities/earlier/insights", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    pb_titles = [i for i in body if i["kind"] == "pb"]
    # "earlier" was the only 10K that existed as of 2025-06-01, so it was that day's best --
    # the fact that "later_and_faster" beats it now must not retroactively erase that.
    assert any(i["activity_id"] == "earlier" for i in pb_titles)


def test_a_later_faster_run_gets_its_own_pb_credit(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn, activity_id="earlier", local_date="2025-06-01",
            sport="running", distance_m=10000.0, duration_s=3200.0, moving_duration_s=3200.0,
        )
        seed_activity(
            conn, activity_id="later_and_faster", local_date="2025-06-15",
            sport="running", distance_m=10000.0, duration_s=3000.0, moving_duration_s=3000.0,
        )

    r = client.get("/api/v1/activities/later_and_faster/insights", headers=auth_headers)
    body = r.json()
    ten_k_pb = [
        i for i in body
        if i["kind"] == "pb" and i["activity_id"] == "later_and_faster"
        and i["detail"]["distance_label"] == "10 km"
    ]
    assert len(ten_k_pb) == 1


def test_no_streak_shown_for_a_lone_unremarkable_activity_with_no_history(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="only_one", local_date="2025-06-01", sport="running")

    r = client.get("/api/v1/activities/only_one/insights", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # A single activity is trivially its own "longest"/"fastest"/PB in every window -- that's
    # honest, not invented -- so effort/pb insights are expected here. A 1-day "streak" is just
    # restating "you ran today," though, so it must not appear at all (rules_activity.py's
    # _MIN_NOTABLE_STREAK_DAYS).
    assert not any(i["kind"] == "streak" for i in body)


def test_current_streak_only_counts_days_up_to_this_activity(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="d1", local_date="2025-06-01", sport="running")
        seed_activity(conn, activity_id="d2", local_date="2025-06-02", sport="running")
        seed_activity(conn, activity_id="d3", local_date="2025-06-03", sport="running")
        seed_activity(conn, activity_id="d4", local_date="2025-06-04", sport="running")
        # A gap, then a later run -- must not extend the streak computed as-of d4.
        seed_activity(conn, activity_id="d7", local_date="2025-06-07", sport="running")

    r = client.get("/api/v1/activities/d4/insights", headers=auth_headers)
    body = r.json()
    streak = next(i for i in body if i["kind"] == "streak")
    assert streak["value_num"] == 4.0
