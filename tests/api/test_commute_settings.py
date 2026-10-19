"""Home and work addresses, commute detection, and the Zwift virtual tag."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.auto_tags import (
    COMMUTE_RADIUS_M,
    apply_auto_tags,
    is_commute_route,
    is_zwift_title,
)
from perseverer.db.schema import activity, route_geom
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.geocoding import GeocodedAddress, GeocodingUnavailable, parse_nominatim_search
from tests.api.conftest import seed_activity

HOME = (47.6000, -122.3000)
WORK = (47.6200, -122.3300)
ELSEWHERE = (47.7000, -122.2000)
FAR = ELSEWHERE


def _ride(
    engine: Engine,
    activity_id: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    name: str | None = None,
    sport: str = "cycling",
    day: str = "2025-06-02",
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id=activity_id, sport=sport, local_date=day)
        conn.execute(
            activity.update()
            .where(activity.c.id == activity_id)
            .values(
                name=name,
                start_time_utc=dt.datetime.fromisoformat(f"{day}T07:{len(activity_id):02d}:00"),
            )
        )
        conn.execute(
            route_geom.insert().values(
                activity_id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_lat=start[0],
                start_lng=start[1],
                end_lat=end[0],
                end_lng=end[1],
                min_lat=min(start[0], end[0]),
                min_lng=min(start[1], end[1]),
                max_lat=max(start[0], end[0]),
                max_lng=max(start[1], end[1]),
            )
        )
        conn.commit()


def _flags(engine: Engine, activity_id: str) -> tuple[bool, bool]:
    with engine.connect() as conn:
        row = conn.execute(
            select(activity.c.is_commute, activity.c.is_virtual).where(activity.c.id == activity_id)
        ).one()
    return bool(row.is_commute), bool(row.is_virtual)


@pytest.fixture
def geocoder() -> Any:
    """Stands in for Nominatim: home and work resolve to fixed points."""
    places = {
        "1 Home St, Seattle": GeocodedAddress("1 Home Street, Seattle, WA, USA", *HOME),
        "2 Work Ave, Seattle": GeocodedAddress("2 Work Avenue, Seattle, WA, USA", *WORK),
    }

    def fake(conn: Any, archive_root: Any, *, athlete_id: str, query: str, **_: Any) -> Any:
        return places.get(query)

    with patch("perseverer.api.routers.commute_settings.geocode_address", side_effect=fake) as m:
        yield m


def _put(client: TestClient, headers: dict[str, str], **body: object) -> Any:
    return client.put("/api/v1/settings/commute", headers=headers, json=body)


class TestPureRules:
    def test_a_route_between_home_and_work_either_way_is_a_commute(self) -> None:
        assert is_commute_route(HOME, WORK, HOME, WORK)
        assert is_commute_route(WORK, HOME, HOME, WORK)
        assert not is_commute_route(HOME, HOME, HOME, WORK)  # a loop from home
        assert not is_commute_route(HOME, ELSEWHERE, HOME, WORK)
        assert not is_commute_route(ELSEWHERE, WORK, HOME, WORK)

    def test_needs_both_addresses_and_both_ends_of_the_route(self) -> None:
        assert not is_commute_route(HOME, WORK, HOME, None)
        assert not is_commute_route(HOME, WORK, None, WORK)
        assert not is_commute_route((None, None), WORK, HOME, WORK)

    def test_close_enough_means_within_the_radius(self) -> None:
        # ~0.0027 degrees of latitude is about 300 m: inside; 0.006 is about 670 m: outside.
        near = (HOME[0] + 0.0027, HOME[1])
        far = (HOME[0] + 0.006, HOME[1])
        assert COMMUTE_RADIUS_M == 400.0
        assert is_commute_route(near, WORK, HOME, WORK)
        assert not is_commute_route(far, WORK, HOME, WORK)

    def test_zwift_in_the_title_any_case(self) -> None:
        assert is_zwift_title("Zwift - Pacer Group Ride: Volcano Flat")
        assert is_zwift_title("morning ZWIFT spin")
        assert not is_zwift_title("Santa Clara Cycling")
        assert not is_zwift_title(None)
        assert not is_zwift_title("")

    def test_parse_nominatim_search(self) -> None:
        found = parse_nominatim_search(
            [{"lat": "47.6", "lon": "-122.3", "display_name": "1 Home Street, Seattle"}]
        )
        assert found == GeocodedAddress("1 Home Street, Seattle", 47.6, -122.3)
        assert parse_nominatim_search([]) is None
        assert parse_nominatim_search({"error": "x"}) is None
        assert parse_nominatim_search([{"lat": "x", "lon": "y"}]) is None


class TestSettings:
    def test_nothing_set_by_default(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        r = client.get("/api/v1/settings/commute", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == {
            "home_address": None,
            "home_label": None,
            "work_address": None,
            "work_label": None,
            "detection_active": False,
            "detected_count": 0,
            "radius_m": 400.0,
        }

    def test_saving_both_addresses_looks_them_up_and_tags_matching_activities(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine, geocoder: Any
    ) -> None:
        _ride(engine, "commute-in", HOME, WORK)
        _ride(engine, "commute-back", WORK, HOME, day="2025-06-02")
        _ride(engine, "leisure", HOME, ELSEWHERE, day="2025-06-03")
        assert _flags(engine, "commute-in") == (False, False)

        r = _put(
            client,
            auth_headers,
            home_address="1 Home St, Seattle",
            work_address="2 Work Ave, Seattle",
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["home_label"] == "1 Home Street, Seattle, WA, USA"
        assert body["work_label"] == "2 Work Avenue, Seattle, WA, USA"
        assert body["detection_active"] is True
        assert body["detected_count"] == 2
        assert _flags(engine, "commute-in")[0] is True
        assert _flags(engine, "commute-back")[0] is True
        assert _flags(engine, "leisure")[0] is False

    def test_one_address_alone_detects_nothing(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine, geocoder: Any
    ) -> None:
        _ride(engine, "r", HOME, WORK)
        body = _put(client, auth_headers, home_address="1 Home St, Seattle").json()
        assert body["detection_active"] is False
        assert body["detected_count"] == 0
        assert _flags(engine, "r")[0] is False

    def test_an_unchanged_address_is_not_looked_up_again(
        self, client: TestClient, auth_headers: dict[str, str], geocoder: Any
    ) -> None:
        both = {"home_address": "1 Home St, Seattle", "work_address": "2 Work Ave, Seattle"}
        _put(client, auth_headers, **both)
        assert geocoder.call_count == 2
        _put(client, auth_headers, **both)
        assert geocoder.call_count == 2
        _put(client, auth_headers, home_address="1 Home St, Seattle", work_address=None)
        assert geocoder.call_count == 2

    def test_clearing_an_address_removes_the_detected_tags(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine, geocoder: Any
    ) -> None:
        _ride(engine, "r", HOME, WORK)
        _put(
            client,
            auth_headers,
            home_address="1 Home St, Seattle",
            work_address="2 Work Ave, Seattle",
        )
        assert _flags(engine, "r")[0] is True
        body = _put(client, auth_headers, home_address="1 Home St, Seattle").json()
        assert body["work_address"] is None and body["detection_active"] is False
        assert _flags(engine, "r")[0] is False

    def test_an_address_that_cannot_be_found_saves_nothing(
        self, client: TestClient, auth_headers: dict[str, str], geocoder: Any
    ) -> None:
        r = _put(
            client, auth_headers, home_address="1 Home St, Seattle", work_address="nowhere at all"
        )
        assert r.status_code == 422
        assert "work address" in r.json()["detail"]
        got = client.get("/api/v1/settings/commute", headers=auth_headers).json()
        assert got["home_address"] is None

    def test_an_unreachable_lookup_service_is_a_502(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        with patch(
            "perseverer.api.routers.commute_settings.geocode_address",
            side_effect=GeocodingUnavailable("down"),
        ):
            r = _put(client, auth_headers, home_address="x")
        assert r.status_code == 502

    def test_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/settings/commute").status_code in (401, 403)
        assert client.put("/api/v1/settings/commute", json={}).status_code in (401, 403)


class TestNeverOverridesTheAthlete:
    def _configure(self, client: TestClient, headers: dict[str, str]) -> None:
        r = _put(
            client, headers, home_address="1 Home St, Seattle", work_address="2 Work Ave, Seattle"
        )
        assert r.status_code == 200

    def test_a_manual_untag_survives_detection(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine, geocoder: Any
    ) -> None:
        _ride(engine, "r", HOME, WORK)
        self._configure(client, auth_headers)
        assert _flags(engine, "r")[0] is True
        client.patch(
            "/api/v1/activities/r/commute", json={"is_commute": False}, headers=auth_headers
        )
        with engine.connect() as conn:
            apply_auto_tags(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
        assert _flags(engine, "r")[0] is False
        # ...and it no longer counts as detected.
        assert (
            client.get("/api/v1/settings/commute", headers=auth_headers).json()["detected_count"]
            == 0
        )

    def test_a_manual_tag_survives_the_addresses_being_removed(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine, geocoder: Any
    ) -> None:
        _ride(engine, "r", ELSEWHERE, FAR)
        client.patch(
            "/api/v1/activities/r/commute", json={"is_commute": True}, headers=auth_headers
        )
        self._configure(client, auth_headers)
        _put(client, auth_headers)
        assert _flags(engine, "r")[0] is True

    def test_a_manual_not_virtual_survives_a_zwift_title(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        _ride(engine, "z", ELSEWHERE, FAR, name="Zwift - Watopia")
        with engine.connect() as conn:
            apply_auto_tags(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
        assert _flags(engine, "z")[1] is True
        client.patch(
            "/api/v1/activities/z/virtual", json={"is_virtual": False}, headers=auth_headers
        )
        with engine.connect() as conn:
            apply_auto_tags(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
        assert _flags(engine, "z")[1] is False


class TestZwift:
    def test_a_zwift_title_is_tagged_virtual_and_other_titles_are_not(self, engine: Engine) -> None:
        _ride(engine, "z1", ELSEWHERE, FAR, name="Zwift - Pacer Group Ride")
        _ride(engine, "z2", ELSEWHERE, FAR, name="lunch ZWIFT spin")
        _ride(engine, "real", ELSEWHERE, FAR, name="Santa Clara Cycling")
        with engine.connect() as conn:
            result = apply_auto_tags(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
        assert result.virtual == 2
        assert [_flags(engine, a)[1] for a in ("z1", "z2", "real")] == [True, True, False]

    def test_renaming_an_activity_retags_it(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        _ride(engine, "r", ELSEWHERE, FAR, name="Evening ride")
        assert _flags(engine, "r")[1] is False
        client.patch(
            "/api/v1/activities/r/name", json={"name": "Zwift - Tempus Fugit"}, headers=auth_headers
        )
        assert _flags(engine, "r")[1] is True
        client.patch(
            "/api/v1/activities/r/name", json={"name": "Back outside"}, headers=auth_headers
        )
        assert _flags(engine, "r")[1] is False

    def test_running_twice_changes_nothing_more(self, engine: Engine) -> None:
        _ride(engine, "z", ELSEWHERE, FAR, name="Zwift")
        with engine.connect() as conn:
            first = apply_auto_tags(conn, athlete_id=DEFAULT_ATHLETE_ID)
            second = apply_auto_tags(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
        assert first.changed == 1 and second.changed == 0
