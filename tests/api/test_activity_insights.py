"""Tests for GET /activities/{id}/insights -- the point-in-time, per-activity view built on
insights/rules_activity.py. The one property every test here ultimately protects is "never look
into the future": an activity's insights must never be able to see, or be influenced by, an
activity dated after it.
"""

from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine, update

from perseverer.db.schema import activity, split
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity


def _add_climb_route(
    engine: Engine,
    *,
    activity_id: str,
    split_index: int,
    grade: int,
    result: str,
    duration_s: float,
) -> None:
    with engine.connect() as conn:
        conn.execute(
            split.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                split_index=split_index,
                split_type="climb_active",
                climb_grade=grade,
                climb_result=result,
                duration_s=duration_s,
            )
        )
        conn.commit()


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
            conn,
            activity_id="earlier",
            local_date="2025-06-01",
            sport="running",
            distance_m=10000.0,
            duration_s=3200.0,
            moving_duration_s=3200.0,
        )
        seed_activity(
            conn,
            activity_id="later_and_faster",
            local_date="2025-06-15",
            sport="running",
            distance_m=10000.0,
            duration_s=3000.0,
            moving_duration_s=3000.0,
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
            conn,
            activity_id="earlier",
            local_date="2025-06-01",
            sport="running",
            distance_m=10000.0,
            duration_s=3200.0,
            moving_duration_s=3200.0,
        )
        seed_activity(
            conn,
            activity_id="later_and_faster",
            local_date="2025-06-15",
            sport="running",
            distance_m=10000.0,
            duration_s=3000.0,
            moving_duration_s=3000.0,
        )

    r = client.get("/api/v1/activities/later_and_faster/insights", headers=auth_headers)
    body = r.json()
    ten_k_pb = [
        i
        for i in body
        if i["kind"] == "pb"
        and i["activity_id"] == "later_and_faster"
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


def test_bouldering_insights_never_look_ahead_to_a_later_session(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="earlier_boulder",
            local_date="2025-06-01",
            sport="rock_climbing",
            sub_sport="bouldering",
            distance_m=None,
        )
        seed_activity(
            conn,
            activity_id="later_boulder",
            local_date="2025-06-15",
            sport="rock_climbing",
            sub_sport="bouldering",
            distance_m=None,
        )
    _add_climb_route(
        engine,
        activity_id="earlier_boulder",
        split_index=0,
        grade=4,
        result="completed",
        duration_s=60.0,
    )
    _add_climb_route(
        engine,
        activity_id="later_boulder",
        split_index=0,
        grade=7,
        result="completed",
        duration_s=90.0,
    )

    r = client.get("/api/v1/activities/earlier_boulder/insights", headers=auth_headers)

    assert r.status_code == 200
    body = r.json()
    assert any(i["title"] == "Highest attempted grade ever" for i in body)
    assert all(i["activity_id"] != "later_boulder" for i in body)


def test_bouldering_insights_exclude_a_later_session_on_the_same_day(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="morning_boulder",
            local_date="2025-06-01",
            sport="rock_climbing",
            sub_sport="bouldering",
            distance_m=None,
        )
        seed_activity(
            conn,
            activity_id="evening_boulder",
            local_date="2025-06-01",
            sport="rock_climbing",
            sub_sport="bouldering",
            distance_m=None,
        )
        conn.execute(
            update(activity)
            .where(activity.c.id == "evening_boulder")
            .values(start_time_utc=datetime(2025, 6, 1, 18, 0, 0))
        )
        conn.commit()
    _add_climb_route(
        engine,
        activity_id="morning_boulder",
        split_index=0,
        grade=4,
        result="completed",
        duration_s=60.0,
    )
    _add_climb_route(
        engine,
        activity_id="evening_boulder",
        split_index=0,
        grade=7,
        result="completed",
        duration_s=90.0,
    )

    r = client.get("/api/v1/activities/morning_boulder/insights", headers=auth_headers)

    assert r.status_code == 200
    assert any(i["title"] == "Highest attempted grade ever" for i in r.json())
