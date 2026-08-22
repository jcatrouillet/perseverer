"""Tests for GET/PUT /settings/hr-zones -- an athlete's own configured HR training zones."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.db.schema import athlete_hr_zone_config


def test_get_returns_all_null_when_unconfigured(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/settings/hr-zones", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "max_hr_bpm": None,
        "threshold_hr_bpm": None,
        "resting_hr_bpm": None,
        "zone1_high_bpm": None,
        "zone2_high_bpm": None,
        "zone3_high_bpm": None,
        "zone4_high_bpm": None,
    }


def test_put_stores_inputs_and_returns_derived_boundaries(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.put(
        "/api/v1/settings/hr-zones",
        json={"max_hr_bpm": 190.0, "threshold_hr_bpm": 170.0, "resting_hr_bpm": 48.0},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["max_hr_bpm"] == 190.0
    assert body["threshold_hr_bpm"] == 170.0
    assert body["resting_hr_bpm"] == 48.0
    assert body["zone1_high_bpm"] is not None
    z1, z2, z3, z4 = (body[f"zone{i}_high_bpm"] for i in range(1, 5))
    assert z1 < z2 <= z3 <= z4

    with engine.connect() as conn:
        row = conn.execute(select(athlete_hr_zone_config)).fetchone()
    assert row is not None
    assert (row.max_hr_bpm, row.threshold_hr_bpm, row.resting_hr_bpm) == (190.0, 170.0, 48.0)


def test_put_upserts_a_second_time_rather_than_duplicating(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    client.put(
        "/api/v1/settings/hr-zones",
        json={"max_hr_bpm": 190.0, "threshold_hr_bpm": 170.0, "resting_hr_bpm": 48.0},
        headers=auth_headers,
    )
    client.put(
        "/api/v1/settings/hr-zones",
        json={"max_hr_bpm": 195.0, "threshold_hr_bpm": 172.0, "resting_hr_bpm": 46.0},
        headers=auth_headers,
    )

    with engine.connect() as conn:
        rows = conn.execute(select(athlete_hr_zone_config)).fetchall()
    assert len(rows) == 1
    assert rows[0].max_hr_bpm == 195.0


def test_get_after_put_reflects_the_stored_config(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.put(
        "/api/v1/settings/hr-zones",
        json={"max_hr_bpm": 190.0, "threshold_hr_bpm": 170.0, "resting_hr_bpm": 48.0},
        headers=auth_headers,
    )
    r = client.get("/api/v1/settings/hr-zones", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["max_hr_bpm"] == 190.0


def test_put_rejects_max_hr_not_greater_than_resting_hr(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put(
        "/api/v1/settings/hr-zones",
        json={"max_hr_bpm": 60.0, "threshold_hr_bpm": 55.0, "resting_hr_bpm": 60.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_put_rejects_threshold_hr_above_max_hr(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put(
        "/api/v1/settings/hr-zones",
        json={"max_hr_bpm": 180.0, "threshold_hr_bpm": 190.0, "resting_hr_bpm": 50.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/settings/hr-zones").status_code in (401, 403)
    assert client.put("/api/v1/settings/hr-zones", json={}).status_code in (401, 403)
