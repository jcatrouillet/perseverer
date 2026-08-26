"""API tests for share.py -- the management endpoints (auth required) and the public
GET /share/{token} (no auth at all). See tests/test_sharing.py for the underlying pure
functions.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.db.schema import day_rollup, share_link
from perseverer.db.seed import DEFAULT_ATHLETE_ID

from .conftest import seed_activity


def test_create_activity_share_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/activities/act1/share")
    assert r.status_code in (401, 403)


def test_create_activity_share_returns_a_public_url(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="act1")

    r = client.post("/api/v1/activities/act1/share", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert "/share/" in body["url"]

    with engine.connect() as conn:
        row = conn.execute(select(share_link).where(share_link.c.id == body["id"])).one()
    assert row.target_type == "activity"
    assert row.target_id == "act1"


def test_create_activity_share_404_for_unknown_activity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post("/api/v1/activities/does-not-exist/share", headers=auth_headers)
    assert r.status_code == 404


def test_create_period_share_requires_period_start_unless_all(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post("/api/v1/periods/month/share", headers=auth_headers)
    assert r.status_code == 422


def test_create_period_share_all_needs_no_period_start(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.post("/api/v1/periods/all/share", headers=auth_headers)
    assert r.status_code == 200
    with engine.connect() as conn:
        row = conn.execute(
            select(share_link).where(share_link.c.id == r.json()["id"])
        ).one()
    assert row.target_type == "period"
    assert row.target_id == "all"


def test_create_period_share_month(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.post(
        "/api/v1/periods/month/share",
        params={"period_start": "2026-06"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    with engine.connect() as conn:
        row = conn.execute(
            select(share_link).where(share_link.c.id == r.json()["id"])
        ).one()
    assert row.target_id == "month:2026-06"


def test_revoke_share_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/share/1/revoke")
    assert r.status_code in (401, 403)


def test_revoke_share(client: TestClient, auth_headers: dict[str, str], engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="act1")
    created = client.post("/api/v1/activities/act1/share", headers=auth_headers).json()

    r = client.post(f"/api/v1/share/{created['id']}/revoke", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"revoked": True}

    # Revoking again is a no-op, not an error.
    r2 = client.post(f"/api/v1/share/{created['id']}/revoke", headers=auth_headers)
    assert r2.json() == {"revoked": False}


def test_public_share_page_requires_no_auth(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="act1")
    created = client.post("/api/v1/activities/act1/share", headers=auth_headers).json()
    token = created["url"].rsplit("/", 1)[-1]

    r = client.get(f"/share/{token}")  # deliberately no auth_headers
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Running" in r.text  # real content rendered (seed_activity's own default sport)


def test_public_share_page_unavailable_for_bogus_token(client: TestClient) -> None:
    r = client.get("/share/not-a-real-token")
    assert r.status_code == 200
    assert "no longer available" in r.text


def test_public_share_page_unavailable_once_revoked(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="act1")
    created = client.post("/api/v1/activities/act1/share", headers=auth_headers).json()
    token = created["url"].rsplit("/", 1)[-1]
    client.post(f"/api/v1/share/{created['id']}/revoke", headers=auth_headers)

    r = client.get(f"/share/{token}")
    assert "no longer available" in r.text


def test_public_share_page_renders_a_period_summary(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        conn.execute(
            day_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-06-15",
                activity_count=3,
                activity_distance_m=15000.0,
                refreshed_at=now,
            )
        )
        conn.commit()

    created = client.post(
        "/api/v1/periods/month/share",
        params={"period_start": "2026-06"},
        headers=auth_headers,
    ).json()
    token = created["url"].rsplit("/", 1)[-1]

    r = client.get(f"/share/{token}")
    assert r.status_code == 200
    assert "15.00 km" in r.text
