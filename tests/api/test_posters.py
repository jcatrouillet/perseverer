"""Year posters: the job (queued -> done | failed), the picture files, the sport list and the
heatmap defaults."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

import polyline as polyline_codec
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine

from perseverer.api.routers.posters import STALE_AFTER
from perseverer.db.schema import poster, route_geom
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.posters import HEIGHT, WIDTH, radius_for_share
from tests.api.conftest import seed_activity

Json = dict[str, Any]


def _with_route(engine: Engine, activity_id: str, lat: float = 48.85, lon: float = 2.35) -> None:
    points = [(lat + i * 0.001, lon + i * 0.0005) for i in range(30)]
    with engine.connect() as conn:
        conn.execute(
            route_geom.insert().values(
                activity_id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                encoded_polyline=polyline_codec.encode(points),
                start_lat=points[0][0],
                start_lng=points[0][1],
            )
        )
        conn.commit()


def _seed(engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", local_date="2025-03-01", sport="running")
        seed_activity(conn, activity_id="a2", local_date="2025-03-05", sport="running")
        seed_activity(conn, activity_id="a3", local_date="2025-04-01", sport="cycling")
        seed_activity(conn, activity_id="a4", local_date="2024-04-01", sport="running")
    _with_route(engine, "a1")
    _with_route(engine, "a2", 48.851, 2.351)
    _with_route(engine, "a3", 48.86, 2.36)
    _with_route(engine, "a4")


def _create(client: TestClient, headers: dict[str, str], **body: object) -> Json:
    payload: Json = {"kind": "calendar", "year": 2025}
    payload.update(body)
    r = client.post("/api/v1/posters", headers=headers, json=payload)
    assert r.status_code == 202, r.text
    return cast(Json, r.json())


def test_poster_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/posters").status_code in (401, 403)


@pytest.mark.parametrize("kind", ["calendar", "circular", "grid"])
def test_poster_is_drawn_and_downloadable(
    client: TestClient,
    engine: Engine,
    auth_headers: dict[str, str],
    test_settings: Any,
    kind: str,
) -> None:
    _seed(engine)
    created = _create(client, auth_headers, kind=kind, sports=["running"])
    assert created["status"] == "queued"

    # The test client runs the background task before returning, so it is already done.
    got = client.get(f"/api/v1/posters/{created['id']}", headers=auth_headers).json()
    assert got["status"] == "done", got
    assert got["activity_count"] == 2  # a1 and a2, not the 2024 run or the ride
    assert got["filename"].startswith(f"Running-2025-all-{kind}-{HEIGHT}-")
    assert got["filename"].endswith(".png")

    file = client.get(f"/api/v1/posters/{created['id']}/file", headers=auth_headers)
    assert file.status_code == 200
    assert file.headers["content-type"] == "image/png"
    assert got["filename"] in file.headers["content-disposition"]
    saved = Path(test_settings.data_dir) / "posters" / f"{created['id']}.png"
    with Image.open(saved) as img:
        assert img.size == (WIDTH, HEIGHT)


def test_heatmap_uses_the_chosen_point_and_range(
    client: TestClient, engine: Engine, auth_headers: dict[str, str]
) -> None:
    _seed(engine)
    created = _create(
        client,
        auth_headers,
        kind="heatmap",
        center_lat=48.85,
        center_lon=2.35,
        radius_km=3.5,
    )
    assert created["center_lat"] == 48.85
    assert created["radius_km"] == 3.5
    got = client.get(f"/api/v1/posters/{created['id']}", headers=auth_headers).json()
    assert got["status"] == "done", got
    assert got["activity_count"] == 3  # every sport


def test_heatmap_needs_both_coordinates(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post(
        "/api/v1/posters",
        headers=auth_headers,
        json={"kind": "heatmap", "year": 2025, "center_lat": 48.0},
    )
    assert r.status_code == 422


def test_no_activity_fails_with_a_reason(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _create(client, auth_headers, kind="calendar")
    got = client.get(f"/api/v1/posters/{created['id']}", headers=auth_headers).json()
    assert got["status"] == "failed"
    assert got["error"]
    assert (
        client.get(f"/api/v1/posters/{created['id']}/file", headers=auth_headers).status_code == 409
    )


def test_virtual_activities_are_left_out_unless_asked(
    client: TestClient, engine: Engine, auth_headers: dict[str, str]
) -> None:
    from sqlalchemy import update

    from perseverer.db.schema import activity

    _seed(engine)
    with engine.connect() as conn:
        conn.execute(update(activity).where(activity.c.id == "a2").values(is_virtual=True))
        conn.commit()
    sports = client.get("/api/v1/posters/sports?year=2025", headers=auth_headers).json()
    assert {s["sport"]: s["activities"] for s in sports} == {"running": 1, "cycling": 1}
    both = client.get(
        "/api/v1/posters/sports?year=2025&include_virtual=true", headers=auth_headers
    ).json()
    assert {s["sport"]: s["activities"] for s in both}["running"] == 2


def test_sports_lists_how_many_have_a_trace(
    client: TestClient, engine: Engine, auth_headers: dict[str, str]
) -> None:
    _seed(engine)
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a5", local_date="2025-05-01", sport="running")
    sports = client.get("/api/v1/posters/sports?year=2025", headers=auth_headers).json()
    running = next(s for s in sports if s["sport"] == "running")
    assert (running["activities"], running["with_route"]) == (3, 2)


def test_heatmap_defaults_to_the_start_area(
    client: TestClient, engine: Engine, auth_headers: dict[str, str]
) -> None:
    _seed(engine)
    d = client.get("/api/v1/posters/heatmap-defaults?year=2025", headers=auth_headers).json()
    assert d["available"] is True
    assert d["activities"] == 3
    assert d["radius_km"] >= 1.0
    assert d["source"] in ("home_address", "profile_home", "activities")
    empty = client.get("/api/v1/posters/heatmap-defaults?year=2019", headers=auth_headers).json()
    assert empty["available"] is False


def test_radius_holds_at_least_half_of_the_starts() -> None:
    starts = [(48.85 + i * 0.01, 2.35) for i in range(10)]  # 0 to ~10 km north
    radius = radius_for_share((48.85, 2.35), starts)
    inside = [s for s in starts if abs(s[0] - 48.85) * 111.32 <= radius]
    assert len(inside) >= 5
    assert radius_for_share((0, 0), []) == 1.0


def test_delete_removes_the_row_and_the_file(
    client: TestClient,
    engine: Engine,
    auth_headers: dict[str, str],
    test_settings: Any,
) -> None:
    _seed(engine)
    created = _create(client, auth_headers, sports=["running"])
    saved = Path(test_settings.data_dir) / "posters" / f"{created['id']}.png"
    assert saved.exists()
    assert (
        client.delete(f"/api/v1/posters/{created['id']}", headers=auth_headers).status_code == 200
    )
    assert not saved.exists()
    assert client.get(f"/api/v1/posters/{created['id']}", headers=auth_headers).status_code == 404
    assert client.get("/api/v1/posters?year=2025", headers=auth_headers).json() == []


def test_a_job_lost_to_a_restart_is_reported_failed(
    client: TestClient, engine: Engine, auth_headers: dict[str, str]
) -> None:
    old = dt.datetime.now(dt.UTC).replace(tzinfo=None) - STALE_AFTER - dt.timedelta(minutes=1)
    with engine.connect() as conn:
        conn.execute(
            poster.insert().values(
                id="01JSTALE00000000000000000A",
                athlete_id=DEFAULT_ATHLETE_ID,
                kind="grid",
                year=2025,
                params="{}",
                status="running",
                created_at=old,
            )
        )
        conn.commit()
    rows = client.get("/api/v1/posters?year=2025", headers=auth_headers).json()
    assert rows[0]["status"] == "failed"
    assert "restarted" in rows[0]["error"]


def test_the_list_is_filtered_by_year_and_newest_first(
    client: TestClient, engine: Engine, auth_headers: dict[str, str]
) -> None:
    _seed(engine)
    first = _create(client, auth_headers, sports=["running"])
    second = _create(client, auth_headers, kind="circular", sports=["running"])
    other = _create(client, auth_headers, year=2024, sports=["running"])
    ids = [p["id"] for p in client.get("/api/v1/posters?year=2025", headers=auth_headers).json()]
    assert ids == [second["id"], first["id"]]
    assert [
        p["id"] for p in client.get("/api/v1/posters?year=2024", headers=auth_headers).json()
    ] == [other["id"]]
