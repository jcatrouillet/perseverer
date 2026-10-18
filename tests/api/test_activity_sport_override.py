"""Tests for PATCH /activities/{id}/sport -- the manual "this sport is wrong" correction (see
sport_override.py's own docstring for the rebuild-survival design this exercises)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.db.schema import activity, activity_sport_override
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

    r = client.patch("/api/v1/activities/target/sport", json={"sport": "  "}, headers=auth_headers)
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
        client.patch("/api/v1/activities/target/race", json={"is_race": True}, headers=auth_headers)

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

        client.patch("/api/v1/activities/target/race", json={"is_race": True}, headers=auth_headers)
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


class TestFuelingOverride:
    """PATCH /activities/{id}/fueling -- the athlete's own carbohydrate/sodium intake logging.
    Unlike sport/race/name, there's no vendor source to correct here at all (see
    sport_override.py's own docstring), so this is pure user input, recorded the same durable,
    rebuild-safe way."""

    def test_override_records_fueling_immediately(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        r = client.patch(
            "/api/v1/activities/target/fueling",
            json={"carbohydrates_g": 60.0, "sodium_mg": 500.0},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json() == {"carbohydrates_g": 60.0, "sodium_mg": 500.0}

        with engine.connect() as conn:
            row = conn.execute(
                select(activity.c.carbohydrates_g, activity.c.sodium_mg).where(
                    activity.c.id == "target"
                )
            ).fetchone()
        assert row is not None
        assert (row.carbohydrates_g, row.sodium_mg) == (60.0, 500.0)

    def test_fueling_override_does_not_disturb_a_separate_name_correction(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        client.patch(
            "/api/v1/activities/target/name", json={"name": "My Title"}, headers=auth_headers
        )
        client.patch(
            "/api/v1/activities/target/fueling",
            json={"carbohydrates_g": 40.0, "sodium_mg": 300.0},
            headers=auth_headers,
        )

        with engine.connect() as conn:
            row = conn.execute(
                select(activity.c.name, activity.c.carbohydrates_g).where(activity.c.id == "target")
            ).fetchone()
        assert row is not None
        assert (row.name, row.carbohydrates_g) == ("My Title", 40.0)

    def test_override_replaces_a_previous_fueling_entry(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        client.patch(
            "/api/v1/activities/target/fueling",
            json={"carbohydrates_g": 40.0, "sodium_mg": 300.0},
            headers=auth_headers,
        )
        r = client.patch(
            "/api/v1/activities/target/fueling",
            json={"carbohydrates_g": 60.0, "sodium_mg": 500.0},
            headers=auth_headers,
        )
        assert r.status_code == 200

        with engine.connect() as conn:
            rows = conn.execute(select(activity_sport_override)).fetchall()
            activity_row = conn.execute(
                select(activity.c.carbohydrates_g, activity.c.sodium_mg).where(
                    activity.c.id == "target"
                )
            ).fetchone()
        assert len(rows) == 1  # upserted, not a second row
        assert (rows[0].carbohydrates_g, rows[0].sodium_mg) == (60.0, 500.0)
        assert activity_row is not None
        assert (activity_row.carbohydrates_g, activity_row.sodium_mg) == (60.0, 500.0)

    def test_override_accepts_only_one_field_at_a_time(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        r = client.patch(
            "/api/v1/activities/target/fueling",
            json={"carbohydrates_g": 60.0},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json() == {"carbohydrates_g": 60.0, "sodium_mg": None}

    def test_override_404s_for_a_missing_activity(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.patch(
            "/api/v1/activities/does-not-exist/fueling",
            json={"carbohydrates_g": 60.0},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_override_422s_for_a_negative_value(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="target", sport="running")

        r = client.patch(
            "/api/v1/activities/target/fueling",
            json={"carbohydrates_g": -5.0},
            headers=auth_headers,
        )
        assert r.status_code == 422


class TestCommuteOverride:
    """PATCH /activities/{id}/commute -- the athlete's own commute tag, used by the Insights
    "Exclude commutes" toggle; durable across a rebuild like the race flag."""

    def test_tags_and_untags_a_commute_and_exposes_it_on_the_activity(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="ride", sport="cycling")

        assert (
            client.get("/api/v1/activities/ride", headers=auth_headers).json()["is_commute"]
            is False
        )
        r = client.patch(
            "/api/v1/activities/ride/commute", json={"is_commute": True}, headers=auth_headers
        )
        assert r.status_code == 200
        assert r.json() == {"is_commute": True}
        assert (
            client.get("/api/v1/activities/ride", headers=auth_headers).json()["is_commute"] is True
        )
        listed = client.get("/api/v1/activities", headers=auth_headers).json()["items"][0]
        assert listed["is_commute"] is True

        client.patch(
            "/api/v1/activities/ride/commute", json={"is_commute": False}, headers=auth_headers
        )
        assert (
            client.get("/api/v1/activities/ride", headers=auth_headers).json()["is_commute"]
            is False
        )

    def test_the_tag_is_recorded_durably_without_disturbing_other_corrections(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="ride", sport="cycling")
        client.patch(
            "/api/v1/activities/ride/sport",
            json={"sport": "cycling", "sub_sport": None},
            headers=auth_headers,
        )
        client.patch(
            "/api/v1/activities/ride/commute", json={"is_commute": True}, headers=auth_headers
        )
        with engine.connect() as conn:
            row = conn.execute(
                select(activity_sport_override.c.sport, activity_sport_override.c.is_commute)
            ).one()
        assert (row.sport, row.is_commute) == ("cycling", True)

    def test_a_rebuild_reapplies_the_tag(self, engine: Engine) -> None:
        from perseverer.db.seed import DEFAULT_ATHLETE_ID
        from perseverer.sport_override import apply_sport_overrides, set_commute_override

        with engine.connect() as conn:
            seed_activity(conn, activity_id="ride", sport="cycling")
            set_commute_override(
                conn, athlete_id=DEFAULT_ATHLETE_ID, activity_id="ride", is_commute=True
            )
            # "Rebuild": the derived row comes back without the tag.
            conn.execute(activity.update().where(activity.c.id == "ride").values(is_commute=False))
            assert apply_sport_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID) == 1
            tagged = conn.execute(select(activity.c.is_commute)).scalar_one()
        assert tagged is True

    def test_404_for_a_missing_activity(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.patch(
            "/api/v1/activities/nope/commute", json={"is_commute": True}, headers=auth_headers
        )
        assert r.status_code == 404


class TestVirtualOverride:
    """PATCH /activities/{id}/virtual -- the athlete's own virtual tag, used by the map
    explorer's "Hide virtual activities" toggle; durable across a rebuild like the commute flag."""

    def test_tags_and_untags_and_exposes_it_on_the_activity_and_the_map(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        from perseverer.db.schema import route_geom
        from perseverer.db.seed import DEFAULT_ATHLETE_ID

        with engine.connect() as conn:
            seed_activity(conn, activity_id="ride", sport="cycling")
            conn.execute(
                route_geom.insert().values(
                    activity_id="ride",
                    athlete_id=DEFAULT_ATHLETE_ID,
                    start_lat=1.0,
                    start_lng=2.0,
                    end_lat=1.0,
                    end_lng=2.0,
                    min_lat=1.0,
                    min_lng=2.0,
                    max_lat=1.0,
                    max_lng=2.0,
                )
            )
            conn.commit()

        assert (
            client.get("/api/v1/activities/ride", headers=auth_headers).json()["is_virtual"]
            is False
        )
        r = client.patch(
            "/api/v1/activities/ride/virtual", json={"is_virtual": True}, headers=auth_headers
        )
        assert r.status_code == 200
        assert r.json() == {"is_virtual": True}
        assert (
            client.get("/api/v1/activities/ride", headers=auth_headers).json()["is_virtual"] is True
        )
        listed = client.get("/api/v1/activities", headers=auth_headers).json()["items"][0]
        assert listed["is_virtual"] is True
        points = client.get("/api/v1/activities/map", headers=auth_headers).json()
        assert [p["is_virtual"] for p in points] == [True]

        client.patch(
            "/api/v1/activities/ride/virtual", json={"is_virtual": False}, headers=auth_headers
        )
        assert (
            client.get("/api/v1/activities/ride", headers=auth_headers).json()["is_virtual"]
            is False
        )

    def test_a_rebuild_reapplies_the_tag(self, engine: Engine) -> None:
        from perseverer.db.seed import DEFAULT_ATHLETE_ID
        from perseverer.sport_override import apply_sport_overrides, set_virtual_override

        with engine.connect() as conn:
            seed_activity(conn, activity_id="ride", sport="cycling")
            set_virtual_override(
                conn, athlete_id=DEFAULT_ATHLETE_ID, activity_id="ride", is_virtual=True
            )
            conn.execute(activity.update().where(activity.c.id == "ride").values(is_virtual=False))
            assert apply_sport_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID) == 1
            tagged = conn.execute(select(activity.c.is_virtual)).scalar_one()
        assert tagged is True

    def test_404_for_a_missing_activity(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.patch(
            "/api/v1/activities/nope/virtual", json={"is_virtual": True}, headers=auth_headers
        )
        assert r.status_code == 404
