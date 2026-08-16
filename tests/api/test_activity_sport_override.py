"""Tests for PATCH /activities/{id}/sport -- the manual "this sport is wrong" correction (see
sport_override.py's own docstring for the rebuild-survival design this exercises)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from sporthealth.db.schema import activity, activity_sport_override
from tests.api.conftest import seed_activity


def test_override_corrects_sport_immediately(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running")

    r = client.patch(
        "/api/v1/activities/target/sport",
        json={"sport": "hiking", "sub_sport": "generic"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"sport": "hiking", "sub_sport": "generic"}

    with engine.connect() as conn:
        row = conn.execute(
            select(activity.c.sport, activity.c.sub_sport).where(activity.c.id == "target")
        ).fetchone()
    assert row is not None
    assert row.sport == "hiking"
    assert row.sub_sport == "generic"


def test_override_is_recorded_by_start_time_not_activity_id(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """The durable record is keyed by start_time_utc, not activity.id -- activity.id is a fresh
    ULID minted on every `sync rebuild`, so it can't be the correction's stable identity."""
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running")
        start = conn.execute(
            select(activity.c.start_time_utc).where(activity.c.id == "target")
        ).scalar_one()

    client.patch(
        "/api/v1/activities/target/sport",
        json={"sport": "walking"},
        headers=auth_headers,
    )

    with engine.connect() as conn:
        row = conn.execute(
            select(activity_sport_override.c.sport, activity_sport_override.c.start_time_utc)
        ).fetchone()
    assert row is not None
    assert row.sport == "walking"
    assert row.start_time_utc == start


def test_override_replaces_a_previous_correction_for_the_same_activity(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running")

    client.patch("/api/v1/activities/target/sport", json={"sport": "hiking"}, headers=auth_headers)
    r = client.patch(
        "/api/v1/activities/target/sport", json={"sport": "walking"}, headers=auth_headers
    )
    assert r.status_code == 200

    with engine.connect() as conn:
        rows = conn.execute(select(activity_sport_override)).fetchall()
        activity_row = conn.execute(
            select(activity.c.sport).where(activity.c.id == "target")
        ).scalar_one()
    assert len(rows) == 1  # upserted, not a second row
    assert rows[0].sport == "walking"
    assert activity_row == "walking"


def test_override_404s_for_a_missing_activity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.patch(
        "/api/v1/activities/does-not-exist/sport",
        json={"sport": "hiking"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_override_422s_for_an_empty_sport(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running")

    r = client.patch(
        "/api/v1/activities/target/sport", json={"sport": "  "}, headers=auth_headers
    )
    assert r.status_code == 422


class TestRaceOverride:
    """PATCH /activities/{id}/race -- the manual "this is/isn't a race" correction, for
    activities where garmin_activity_summary.py's eventTypeId heuristic misses a real race the
    athlete never flagged inside the Garmin Connect app itself."""

    def test_override_marks_an_activity_as_a_race_immediately(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        r = client.patch(
            "/api/v1/activities/target/race", json={"is_race": True}, headers=auth_headers
        )
        assert r.status_code == 200
        assert r.json() == {"is_race": True}

        with engine.connect() as conn:
            row = conn.execute(
                select(activity.c.is_race).where(activity.c.id == "target")
            ).fetchone()
        assert row is not None
        assert row.is_race is True

    def test_override_can_unmark_a_race(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running", is_race=True)

        r = client.patch(
            "/api/v1/activities/target/race", json={"is_race": False}, headers=auth_headers
        )
        assert r.status_code == 200

        with engine.connect() as conn:
            row = conn.execute(
                select(activity.c.is_race).where(activity.c.id == "target")
            ).fetchone()
        assert row is not None
        assert row.is_race is False

    def test_race_override_does_not_disturb_a_separate_sport_correction(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        """Both corrections live on the same activity_sport_override row (keyed by
        athlete/start_time_utc) -- setting one must never blank out the other."""
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        client.patch(
            "/api/v1/activities/target/sport", json={"sport": "hiking"}, headers=auth_headers
        )
        client.patch(
            "/api/v1/activities/target/race", json={"is_race": True}, headers=auth_headers
        )

        with engine.connect() as conn:
            row = conn.execute(
                select(activity.c.sport, activity.c.is_race).where(activity.c.id == "target")
            ).fetchone()
            override_row = conn.execute(
                select(activity_sport_override.c.sport, activity_sport_override.c.is_race)
            ).fetchone()
        assert row is not None
        assert (row.sport, row.is_race) == ("hiking", True)
        assert override_row is not None
        assert (override_row.sport, override_row.is_race) == ("hiking", True)

    def test_override_404s_for_a_missing_activity(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.patch(
            "/api/v1/activities/does-not-exist/race",
            json={"is_race": True},
            headers=auth_headers,
        )
        assert r.status_code == 404


class TestNameOverride:
    """PATCH /activities/{id}/name -- the manual "this title is wrong" correction, for
    activities where Garmin's own name is just as generic a template as the FIT-derived default
    (see sport_override.py's own docstring for why there's no safe automatic fix for this)."""

    def test_override_renames_an_activity_immediately(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        r = client.patch(
            "/api/v1/activities/target/name",
            json={"name": "Santa Clara - Race Pace Run"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json() == {"name": "Santa Clara - Race Pace Run"}

        with engine.connect() as conn:
            row = conn.execute(select(activity.c.name).where(activity.c.id == "target")).fetchone()
        assert row is not None
        assert row.name == "Santa Clara - Race Pace Run"

    def test_name_override_does_not_disturb_a_separate_race_correction(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        client.patch(
            "/api/v1/activities/target/race", json={"is_race": True}, headers=auth_headers
        )
        client.patch(
            "/api/v1/activities/target/name", json={"name": "My Title"}, headers=auth_headers
        )

        with engine.connect() as conn:
            row = conn.execute(
                select(activity.c.name, activity.c.is_race).where(activity.c.id == "target")
            ).fetchone()
        assert row is not None
        assert (row.name, row.is_race) == ("My Title", True)

    def test_override_replaces_a_previous_name_correction(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        client.patch(
            "/api/v1/activities/target/name", json={"name": "First Title"}, headers=auth_headers
        )
        client.patch(
            "/api/v1/activities/target/name", json={"name": "Second Title"}, headers=auth_headers
        )

        with engine.connect() as conn:
            rows = conn.execute(select(activity_sport_override)).fetchall()
            activity_row = conn.execute(
                select(activity.c.name).where(activity.c.id == "target")
            ).scalar_one()
        assert len(rows) == 1  # upserted, not a second row
        assert rows[0].name == "Second Title"
        assert activity_row == "Second Title"

    def test_override_404s_for_a_missing_activity(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.patch(
            "/api/v1/activities/does-not-exist/name",
            json={"name": "x"},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_override_422s_for_an_empty_name(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        r = client.patch(
            "/api/v1/activities/target/name", json={"name": "  "}, headers=auth_headers
        )
        assert r.status_code == 422
