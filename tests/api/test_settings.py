"""Tests for GET/PUT /settings/hr-zones -- an athlete's own configured HR training zones -- and
the Garmin status/login/sync, rebuild, and bulk-import endpoints below.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
from sqlalchemy import Connection, Engine, select

import perseverer.api.routers.settings as settings_router
from perseverer.config import Settings
from perseverer.db.schema import athlete_hr_zone_config, ingest_run
from perseverer.db.seed import DEFAULT_ATHLETE_ID


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


# --- GET /settings/garmin/status ----------------------------------------------------------


def test_garmin_status_no_token_store_no_sync_yet(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/settings/garmin/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["token_store_present"] is False
    assert body["token_store_age_days"] is None
    assert body["last_sync_status"] is None


def test_garmin_status_reports_token_store_age_and_last_sync(
    client: TestClient, auth_headers: dict[str, str], test_settings: Settings, engine: Engine
) -> None:
    test_settings.garmin_tokenstore_dir.mkdir(parents=True)
    (test_settings.garmin_tokenstore_dir / "token.json").write_text("{}")

    with engine.connect() as conn:
        conn.execute(
            ingest_run.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                source="garmin_connect",
                started_at=dt.datetime(2026, 1, 1, 0, 0, 0),
                finished_at=dt.datetime(2026, 1, 1, 0, 5, 0),
                status="success",
                items_seen=5,
                items_new=2,
            )
        )
        conn.commit()

    r = client.get("/api/v1/settings/garmin/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["token_store_present"] is True
    assert body["token_store_age_days"] == 0
    assert body["last_sync_status"] == "success"
    assert body["last_sync_error"] is None
    assert body["staleness_severity"] is None  # last sync succeeded -- nothing to warn about
    assert body["staleness_message"] is None


def test_garmin_status_reports_staleness_once_syncs_start_failing(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """No real Garmin token expiry is readable client-side (see login_with_credentials's own
    docstring) -- this staleness signal, already computed daily by the worker, is the practical
    substitute: the first real sign a session needs re-establishing is the next sync failing."""
    with engine.connect() as conn:
        conn.execute(
            ingest_run.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                source="garmin_connect",
                started_at=dt.datetime(2026, 1, 1, 0, 0, 0),
                finished_at=dt.datetime(2026, 1, 1, 0, 5, 0),
                status="failed",
                items_seen=0,
                items_new=0,
                errors=json.dumps([{"error": "GarminAuthRequired"}]),
            )
        )
        conn.commit()

    r = client.get("/api/v1/settings/garmin/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["staleness_severity"] == "warning"
    assert body["staleness_message"]


# --- POST /settings/garmin/login -- login_with_credentials is patched at the module level so
# no real Garmin network access is ever attempted. ------------------------------------------


def test_garmin_login_success(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_router, "login_with_credentials", lambda *a, **kw: None)
    r = client.post(
        "/api/v1/settings/garmin/login",
        json={"username": "me@example.com", "password": "hunter2"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}


def test_garmin_login_wrong_credentials_returns_400_not_401(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not 401 -- deliberately, see the endpoint's own docstring: this app's frontend treats
    any 401 as "your own Perseverer session expired" and force-logs the athlete out, which
    must never happen just because they mistyped their Garmin password."""

    def raise_auth_error(*a: object, **kw: object) -> None:
        raise GarminConnectAuthenticationError("Authentication failed")

    monkeypatch.setattr(settings_router, "login_with_credentials", raise_auth_error)
    r = client.post(
        "/api/v1/settings/garmin/login",
        json={"username": "me@example.com", "password": "wrong"},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_garmin_login_mfa_required_returns_422_with_cli_hint(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_mfa_error(*a: object, **kw: object) -> None:
        raise GarminConnectAuthenticationError("MFA Required but no prompt_mfa mechanism supplied")

    monkeypatch.setattr(settings_router, "login_with_credentials", raise_mfa_error)
    r = client.post(
        "/api/v1/settings/garmin/login",
        json={"username": "me@example.com", "password": "hunter2"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert "sync auth login" in r.json()["detail"]


def test_garmin_login_rate_limited_returns_429(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_rate_limit(*a: object, **kw: object) -> None:
        raise GarminConnectTooManyRequestsError("429")

    monkeypatch.setattr(settings_router, "login_with_credentials", raise_rate_limit)
    r = client.post(
        "/api/v1/settings/garmin/login",
        json={"username": "me@example.com", "password": "hunter2"},
        headers=auth_headers,
    )
    assert r.status_code == 429


# --- POST /settings/garmin/sync -- sync_garmin_connect is patched with a fake that mimics its
# own real ingest_run bookkeeping, proving the endpoint's background task actually runs it with
# the right args, without any real Garmin network access. ------------------------------------


def test_garmin_sync_trigger_runs_in_background_and_writes_ingest_run(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
) -> None:
    calls: list[str] = []

    def fake_sync_garmin_connect(
        conn: Connection,
        archive_root: object,
        parquet_dir: object,
        tokenstore_dir: object,
        *,
        athlete_id: str,
        **kw: object,
    ) -> None:
        calls.append(athlete_id)
        conn.execute(
            ingest_run.insert().values(
                athlete_id=athlete_id,
                source="garmin_connect",
                started_at=dt.datetime(2026, 1, 1, 0, 0, 0),
                finished_at=dt.datetime(2026, 1, 1, 0, 0, 1),
                status="success",
                items_seen=0,
                items_new=0,
            )
        )
        conn.commit()

    monkeypatch.setattr(settings_router, "sync_garmin_connect", fake_sync_garmin_connect)

    r = client.post("/api/v1/settings/garmin/sync", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"triggered": True}
    assert calls == [DEFAULT_ATHLETE_ID]

    with engine.connect() as conn:
        row = conn.execute(
            select(ingest_run).where(ingest_run.c.source == "garmin_connect")
        ).fetchone()
    assert row is not None
    assert row.status == "success"


# --- POST /settings/rebuild -- runs the real rebuild_database_tracked against an empty local
# archive; no Garmin/network access involved at all. -------------------------------------------


def test_rebuild_trigger_writes_ingest_run(
    client: TestClient, auth_headers: dict[str, str], test_settings: Settings, engine: Engine
) -> None:
    test_settings.raw_archive_dir.mkdir(parents=True, exist_ok=True)
    test_settings.parquet_dir.mkdir(parents=True, exist_ok=True)

    r = client.post("/api/v1/settings/rebuild", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"triggered": True}

    with engine.connect() as conn:
        row = conn.execute(select(ingest_run).where(ingest_run.c.source == "rebuild")).fetchone()
    assert row is not None
    assert row.status == "success"


# --- POST /settings/import/bulk-export -- a real (minimal) Strava export zip, uploaded via
# TestClient's own multipart support. -----------------------------------------------------------


def _minimal_strava_export_zip() -> bytes:
    header = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,"
        "Moving Time,Elevation Gain,Calories,Relative Effort,Filename\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", header)
    return buf.getvalue()


def test_bulk_export_upload_rejects_non_zip(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/settings/import/bulk-export",
        headers=auth_headers,
        data={"kind": "strava"},
        files={"file": ("export.txt", b"not a zip", "text/plain")},
    )
    assert r.status_code == 422


def test_bulk_export_upload_strava_writes_ingest_run(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.post(
        "/api/v1/settings/import/bulk-export",
        headers=auth_headers,
        data={"kind": "strava"},
        files={"file": ("export.zip", _minimal_strava_export_zip(), "application/zip")},
    )
    assert r.status_code == 200
    assert r.json() == {"triggered": True}

    with engine.connect() as conn:
        row = conn.execute(
            select(ingest_run).where(ingest_run.c.source == "strava_export")
        ).fetchone()
    assert row is not None
    assert row.status == "success"


# --- GET /settings/jobs/latest ------------------------------------------------------------


def test_latest_job_no_row_returns_null(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get(
        "/api/v1/settings/jobs/latest", params={"source": "rebuild"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() is None


def test_latest_job_returns_error_details(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        conn.execute(
            ingest_run.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                source="garmin_connect",
                started_at=dt.datetime(2026, 1, 1, 0, 0, 0),
                finished_at=dt.datetime(2026, 1, 1, 0, 0, 5),
                status="failed",
                items_seen=1,
                items_new=0,
                errors=json.dumps([{"error": "429 from Garmin"}]),
            )
        )
        conn.commit()

    r = client.get(
        "/api/v1/settings/jobs/latest", params={"source": "garmin_connect"}, headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_count"] == 1
    assert body["first_error"] == "429 from Garmin"
