"""POST/DELETE/GET /planned-workouts/{id}/route -- a GPX route on a planned running workout."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.db.schema import raw_object

GPX = (
    b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">'
    b"<trk><name>Dam loop</name><trkseg>"
    b'<trkpt lat="37.000" lon="-122.0"><ele>100</ele></trkpt>'
    b'<trkpt lat="37.005" lon="-122.0"><ele>120</ele></trkpt>'
    b'<trkpt lat="37.010" lon="-122.0"><ele>140</ele></trkpt>'
    b"</trkseg></trk></gpx>"
)


def _create(client: TestClient, headers: dict[str, str], sport: str = "running") -> int:
    body: dict[str, Any] = {"local_date": "2026-10-10", "sport": sport, "name": "Long run"}
    if sport == "running":
        body["source_text"] = "60m easy"
    else:
        body["duration_minutes"] = 30
    r = client.post("/api/v1/planned-workouts", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return int(r.json()["id"])


def _upload(client: TestClient, headers: dict[str, str], wid: int, content: bytes = GPX) -> Any:
    return client.post(
        f"/api/v1/planned-workouts/{wid}/route",
        files={"file": ("dam.gpx", content, "application/gpx+xml")},
        headers=headers,
    )


def test_attach_replace_download_and_remove(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    wid = _create(client, auth_headers)
    assert (
        client.get(f"/api/v1/planned-workouts/{wid}", headers=auth_headers).json()["route"] is None
    )

    r = _upload(client, auth_headers, wid)
    assert r.status_code == 200, r.text
    route = r.json()["route"]
    assert route["name"] == "Dam loop"
    assert route["distance_m"] == pytest.approx(1112, rel=0.01)
    assert route["elevation_gain_m"] == pytest.approx(40.0)
    assert route["polyline"]

    # The original file is archived raw and downloadable byte for byte.
    with engine.connect() as conn:
        kinds = [k for (k,) in conn.execute(select(raw_object.c.kind))]
    assert kinds == ["planned_workout_gpx"]
    dl = client.get(f"/api/v1/planned-workouts/{wid}/route.gpx", headers=auth_headers)
    assert dl.status_code == 200 and dl.content == GPX

    # Saving the workout again does not drop the route.
    saved = client.put(
        f"/api/v1/planned-workouts/{wid}",
        json={"sport": "running", "name": "Long run", "source_text": "70m easy"},
        headers=auth_headers,
    )
    assert saved.status_code == 200
    assert saved.json()["route"]["name"] == "Dam loop"

    removed = client.delete(f"/api/v1/planned-workouts/{wid}/route", headers=auth_headers)
    assert removed.status_code == 200 and removed.json()["route"] is None
    assert (
        client.get(f"/api/v1/planned-workouts/{wid}/route.gpx", headers=auth_headers).status_code
        == 404
    )
    with engine.connect() as conn:  # the archive is never deleted
        assert conn.execute(select(raw_object.c.id)).fetchall()


def test_rejects_bad_files_and_non_running_workouts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    wid = _create(client, auth_headers)
    assert _upload(client, auth_headers, wid, b"not a gpx").status_code == 400
    assert _upload(client, auth_headers, wid, b"x" * (5 * 1024 * 1024 + 10)).status_code == 413
    yoga = _create(client, auth_headers, sport="yoga")
    assert _upload(client, auth_headers, yoga).status_code == 400
    assert _upload(client, auth_headers, 99999).status_code == 404


def test_route_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/api/v1/planned-workouts/1/route", files={"file": ("a.gpx", GPX, "application/gpx+xml")}
    )
    assert r.status_code in (401, 503)
