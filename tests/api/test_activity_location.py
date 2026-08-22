"""Tests for GET /activities/{id}/location. The actual Nominatim fetch/archive/store logic is
exhaustively covered by tests/test_geocoding.py against a mocked HTTP transport -- these tests
only check the router's own responsibilities: 404, the no-GPS "unavailable" case, and that a
cache miss returns immediately (never blocking on the background fetch) while still scheduling
one -- see get_activity_location's own docstring for why this had to be non-blocking.
`get_or_fetch_activity_location` is monkeypatched here rather than mocking HTTP a second time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine

from tests.api.conftest import seed_activity
from tests.api.test_activities import _add_route


def test_404_for_unknown_activity(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/activities/doesnotexist/location", headers=auth_headers)
    assert r.status_code == 404


def test_unavailable_when_activity_has_no_gps_start_point(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    # No _add_route() call -- no route_geom row at all for this activity.

    r = client.get("/api/v1/activities/a1/location", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["location_name"] is None


def test_cache_miss_returns_unavailable_immediately_and_schedules_a_background_fetch(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_route(engine, activity_id="a1", start_lat=37.36, start_lng=-121.97)

    calls: list[tuple[float, float]] = []

    def fake_fetch(
        conn: Connection,
        archive_root: Path,
        *,
        athlete_id: str,
        activity_id: str,
        lat: float,
        lon: float,
    ) -> str:
        calls.append((lat, lon))
        return "Sunnyvale, California"

    monkeypatch.setattr(
        "perseverer.api.routers.activities.get_or_fetch_activity_location", fake_fetch
    )

    # The response must never wait on the (mocked-slow-in-reality) fetch -- TestClient still
    # runs the scheduled background task to completion before returning, so by the time we get
    # here the fake fetch has definitely already run once, proving it was scheduled at all.
    r = client.get("/api/v1/activities/a1/location", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"available": False, "location_name": None}
    assert calls == [(37.36, -121.97)]


def test_second_request_reads_the_now_cached_value(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_route(engine, activity_id="a1", start_lat=37.36, start_lng=-121.97)

    def fake_fetch(
        conn: Connection,
        archive_root: Path,
        *,
        athlete_id: str,
        activity_id: str,
        lat: float,
        lon: float,
    ) -> str:
        from perseverer.geocoding import _store

        _store(conn, athlete_id, activity_id, "Sunnyvale, California")
        conn.commit()
        return "Sunnyvale, California"

    monkeypatch.setattr(
        "perseverer.api.routers.activities.get_or_fetch_activity_location", fake_fetch
    )

    first = client.get("/api/v1/activities/a1/location", headers=auth_headers)
    assert first.json()["available"] is False  # first-ever view: nothing cached yet

    second = client.get("/api/v1/activities/a1/location", headers=auth_headers)
    assert second.json() == {"available": True, "location_name": "Sunnyvale, California"}
