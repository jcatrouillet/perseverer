"""GET/PUT /settings/sync-schedule."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import Engine, update

from perseverer.db.schema import athlete
from perseverer.db.seed import DEFAULT_ATHLETE_ID

URL = "/api/v1/settings/sync-schedule"


def _by_source(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["source"]: s for s in body["sources"]}


def test_every_source_listed_with_the_default_time(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = client.get(URL, headers=auth_headers).json()
    assert [s["source"] for s in body["sources"]] == ["garmin_connect", "eufy", "kaya"]
    assert body["window_minutes"] == 5
    garmin = _by_source(body)["garmin_connect"]
    assert (garmin["time"], garmin["is_default"], garmin["enabled"]) == ("04:15", True, True)
    assert garmin["label"] == "Garmin Connect"
    assert garmin["configured"] is False
    assert garmin["next_run_at"] is None  # not connected: it never runs


def test_a_connected_source_has_a_next_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    with patch("perseverer.api.routers.sync_schedule.source_configured", return_value=True):
        garmin = _by_source(client.get(URL, headers=auth_headers).json())["garmin_connect"]
    assert garmin["configured"] is True
    assert garmin["next_run_at"] is not None


def test_put_sets_a_time_and_switches_off_and_back_to_default(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put(
        URL,
        headers=auth_headers,
        json={
            "sources": [
                {"source": "garmin_connect", "time": "07:30", "enabled": True},
                {"source": "kaya", "time": None, "enabled": False},
            ]
        },
    )
    assert r.status_code == 200
    got = _by_source(r.json())
    assert (got["garmin_connect"]["time"], got["garmin_connect"]["is_default"]) == ("07:30", False)
    assert (got["kaya"]["enabled"], got["kaya"]["is_default"]) == (False, True)
    assert got["eufy"]["is_default"] is True  # not listed: untouched

    again = client.put(
        URL,
        headers=auth_headers,
        json={"sources": [{"source": "garmin_connect", "time": None, "enabled": True}]},
    )
    assert _by_source(again.json())["garmin_connect"]["is_default"] is True
    assert client.get(URL, headers=auth_headers).json() == again.json()


def test_put_rejects_a_bad_time_or_source(client: TestClient, auth_headers: dict[str, str]) -> None:
    for entry in (
        {"source": "garmin_connect", "time": "7:30"},
        {"source": "garmin_connect", "time": "25:00"},
        {"source": "strava", "time": "07:30"},
    ):
        r = client.put(URL, headers=auth_headers, json={"sources": [entry]})
        assert r.status_code == 422, entry


def test_a_chosen_time_is_on_the_athletes_clock_and_a_default_is_shown_on_it(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        conn.execute(
            update(athlete).where(athlete.c.id == DEFAULT_ATHLETE_ID).values(timezone="Asia/Tokyo")
        )
        conn.commit()
    client.put(
        URL,
        headers=auth_headers,
        json={"sources": [{"source": "eufy", "time": "08:00", "enabled": True}]},
    )
    got = _by_source(client.get(URL, headers=auth_headers).json())
    assert got["eufy"]["time"] == "08:00"
    assert got["eufy"]["timezone"] == "Asia/Tokyo"
    # The default 04:15 UTC, shown on Tokyo's clock (UTC+9).
    assert got["garmin_connect"]["time"] == "13:15"
    assert got["garmin_connect"]["is_default"] is True


def test_the_garmin_activity_check_interval_is_saved_kept_and_cleared(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    sources = _by_source(client.get(URL, headers=auth_headers).json())
    assert sources["garmin_connect"]["activity_check_hours"] is None  # off by default
    assert sources["eufy"]["activity_check_hours"] is None

    def put(item: dict[str, Any]) -> dict[str, Any]:
        r = client.put(URL, headers=auth_headers, json={"sources": [item]})
        assert r.status_code == 200
        return _by_source(r.json())["garmin_connect"]

    with patch("perseverer.api.routers.sync_schedule.source_configured", return_value=True):
        on = put({"source": "garmin_connect", "time": None, "activity_check_hours": 1})
        assert on["activity_check_hours"] == 1
        assert on["next_activity_check_at"] is not None
        # A request that does not mention it leaves it alone.
        kept = put({"source": "garmin_connect", "time": "07:30", "enabled": True})
        assert kept["activity_check_hours"] == 1
        off = put({"source": "garmin_connect", "time": "07:30", "activity_check_hours": None})
    assert off["activity_check_hours"] is None
    assert off["next_activity_check_at"] is None


def test_the_activity_check_interval_is_garmin_only_and_bounded(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    for item in (
        {"source": "eufy", "activity_check_hours": 1},
        {"source": "garmin_connect", "activity_check_hours": 0},
        {"source": "garmin_connect", "activity_check_hours": 25},
    ):
        r = client.put(URL, headers=auth_headers, json={"sources": [item]})
        assert r.status_code == 422, item


def test_requires_auth(client: TestClient) -> None:
    assert client.get(URL).status_code in (401, 403)
