"""Tests for GET/PUT /settings/hr-zones -- an athlete's own configured HR training zones -- and
the Garmin status/login/sync, rebuild, and bulk-import endpoints below.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
from sqlalchemy import Connection, Engine, select

import perseverer.api.routers.settings as settings_router
from perseverer.adapters.eufy import EufyAuthError, EufyClient
from perseverer.auth.lockout import MAX_FAILED_ATTEMPTS
from perseverer.auth.passwords import hash_password
from perseverer.config import Settings
from perseverer.db.schema import (
    activity_metric,
    athlete,
    athlete_eufy_config,
    athlete_hr_zone_config,
    athlete_running_load_config,
    fitness_daily_rollup,
    ingest_run,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.metrics.registry import get_or_register_metric

from .conftest import seed_activity


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


# --- GET/PUT /settings/running-load --------------------------------------------------------


def test_running_load_get_returns_null_when_unconfigured(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/settings/running-load", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"threshold_pace_sec_per_km": None}


def test_running_load_put_stores_and_returns_the_value(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.put(
        "/api/v1/settings/running-load",
        json={"threshold_pace_sec_per_km": 308.0},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"threshold_pace_sec_per_km": 308.0}

    with engine.connect() as conn:
        row = conn.execute(select(athlete_running_load_config)).fetchone()
    assert row is not None
    assert row.threshold_pace_sec_per_km == 308.0


def test_running_load_put_upserts_a_second_time_rather_than_duplicating(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    client.put(
        "/api/v1/settings/running-load",
        json={"threshold_pace_sec_per_km": 308.0},
        headers=auth_headers,
    )
    client.put(
        "/api/v1/settings/running-load",
        json={"threshold_pace_sec_per_km": 300.0},
        headers=auth_headers,
    )
    with engine.connect() as conn:
        rows = conn.execute(select(athlete_running_load_config)).fetchall()
    assert len(rows) == 1
    assert rows[0].threshold_pace_sec_per_km == 300.0


def test_running_load_get_after_put_reflects_the_stored_config(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.put(
        "/api/v1/settings/running-load",
        json={"threshold_pace_sec_per_km": 300.0},
        headers=auth_headers,
    )
    r = client.get("/api/v1/settings/running-load", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["threshold_pace_sec_per_km"] == 300.0


def test_running_load_put_rejects_non_positive_value(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put(
        "/api/v1/settings/running-load",
        json={"threshold_pace_sec_per_km": 0.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_running_load_put_immediately_refreshes_the_fitness_rollup(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """Saving a threshold pace must not look like a no-op until the next sync -- see
    api/routers/settings.py's own module docstring."""
    with engine.connect() as conn:
        seed_activity(
            conn, activity_id="run1", local_date="2025-06-01", moving_duration_s=3600.0
        )
        get_or_register_metric(
            conn,
            metric_key=AVG_GAP_METRIC_KEY,
            source="perseverer",
            display_name="Average Grade Adjusted Pace",
            unit_si="m/s",
            category="performance",
            value_type="numeric",
        )
        conn.execute(
            activity_metric.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="run1",
                metric_key=AVG_GAP_METRIC_KEY,
                value_num=1000.0 / 300.0,  # exactly 5:00/km
                source="perseverer",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()

    r = client.put(
        "/api/v1/settings/running-load",
        json={"threshold_pace_sec_per_km": 300.0},
        headers=auth_headers,
    )
    assert r.status_code == 200

    with engine.connect() as conn:
        row = conn.execute(
            select(fitness_daily_rollup).where(
                fitness_daily_rollup.c.local_date == "2025-06-01"
            )
        ).fetchone()
    assert row is not None
    assert abs(row.training_load - 100.0) < 1e-9  # 1h exactly at threshold -> rTSS 100


def test_running_load_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/settings/running-load").status_code in (401, 403)
    assert client.put("/api/v1/settings/running-load", json={}).status_code in (401, 403)


# --- GET/POST/DELETE /settings/calendar-feed ----------------------------------------------


def test_calendar_feed_disabled_by_default(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/settings/calendar-feed", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "created_at": None}


def test_calendar_feed_publish_then_status_then_unpublish(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    posted = client.post("/api/v1/settings/calendar-feed", headers=auth_headers)
    assert posted.status_code == 200
    url = posted.json()["url"]
    assert "/share/calendar/" in url
    assert url.endswith(".ics")

    status = client.get("/api/v1/settings/calendar-feed", headers=auth_headers)
    assert status.json()["enabled"] is True
    assert status.json()["created_at"] is not None

    deleted = client.delete("/api/v1/settings/calendar-feed", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"enabled": False, "created_at": None}

    status_after = client.get("/api/v1/settings/calendar-feed", headers=auth_headers)
    assert status_after.json()["enabled"] is False


def test_calendar_feed_post_rotates_to_a_new_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    first = client.post("/api/v1/settings/calendar-feed", headers=auth_headers).json()["url"]
    second = client.post("/api/v1/settings/calendar-feed", headers=auth_headers).json()["url"]
    assert first != second

    old_token = first.rsplit("/", 1)[-1].removesuffix(".ics")
    assert client.get(f"/share/calendar/{old_token}.ics").status_code == 404


def test_calendar_feed_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/settings/calendar-feed").status_code in (401, 403)
    assert client.post("/api/v1/settings/calendar-feed").status_code in (401, 403)
    assert client.delete("/api/v1/settings/calendar-feed").status_code in (401, 403)


# --- GET/PUT /settings/email-reports + POST .../test ------------------------------------------


def test_email_reports_default_is_off_and_reports_smtp_unconfigured(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/settings/email-reports", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "weekly_enabled": False,
        "monthly_enabled": False,
        "smtp_configured": False,  # test_settings fixture sets no PERSEVERER_SMTP_*
        "recipient_email": None,
    }


def test_email_reports_put_upserts_and_reflects_the_profile_email(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(email="jerome@example.com")
        )
        conn.commit()

    r = client.put(
        "/api/v1/settings/email-reports",
        headers=auth_headers,
        json={"weekly_enabled": True, "monthly_enabled": False},
    )
    assert r.status_code == 200
    assert r.json()["weekly_enabled"] is True
    assert r.json()["recipient_email"] == "jerome@example.com"

    # Re-PUT updates in place, no duplicate row.
    r2 = client.put(
        "/api/v1/settings/email-reports",
        headers=auth_headers,
        json={"weekly_enabled": False, "monthly_enabled": True},
    )
    assert r2.json()["weekly_enabled"] is False
    assert r2.json()["monthly_enabled"] is True
    assert client.get("/api/v1/settings/email-reports", headers=auth_headers).json() == {
        "weekly_enabled": False,
        "monthly_enabled": True,
        "smtp_configured": False,
        "recipient_email": "jerome@example.com",
    }


def test_email_reports_test_endpoint_400_when_smtp_unconfigured(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post("/api/v1/settings/email-reports/test", headers=auth_headers)
    assert r.status_code == 400


def test_email_reports_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/settings/email-reports").status_code in (401, 403)
    assert client.put("/api/v1/settings/email-reports", json={}).status_code in (401, 403)
    assert client.post("/api/v1/settings/email-reports/test").status_code in (401, 403)


# --- GET/PUT /settings/profile ------------------------------------------------------------


def test_profile_returns_all_null_when_unconfigured(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/settings/profile", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"birthdate": None, "height_cm": None, "sex": None, "email": None}


def test_put_profile_stores_and_returns_the_values(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.put(
        "/api/v1/settings/profile",
        json={
            "birthdate": "1990-01-01",
            "height_cm": 178.0,
            "sex": "male",
            "email": "erwan@example.com",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {
        "birthdate": "1990-01-01",
        "height_cm": 178.0,
        "sex": "male",
        "email": "erwan@example.com",
    }

    with engine.connect() as conn:
        row = conn.execute(
            select(athlete.c.birthdate, athlete.c.height_cm, athlete.c.sex, athlete.c.email)
        ).fetchone()
    assert row is not None
    assert (row.birthdate, row.height_cm, row.sex, row.email) == (
        "1990-01-01",
        178.0,
        "male",
        "erwan@example.com",
    )


def test_put_profile_rejects_an_invalid_email(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put(
        "/api/v1/settings/profile", json={"email": "not-an-email"}, headers=auth_headers
    )
    assert r.status_code == 422


def test_put_profile_rejects_a_future_birthdate(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put(
        "/api/v1/settings/profile", json={"birthdate": "2999-01-01"}, headers=auth_headers
    )
    assert r.status_code == 422


def test_put_profile_rejects_out_of_range_height(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put("/api/v1/settings/profile", json={"height_cm": 900.0}, headers=auth_headers)
    assert r.status_code == 422


def test_put_profile_rejects_invalid_sex(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.put("/api/v1/settings/profile", json={"sex": "other"}, headers=auth_headers)
    assert r.status_code == 422


def test_profile_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/settings/profile").status_code in (401, 403)
    assert client.put("/api/v1/settings/profile", json={}).status_code in (401, 403)


# --- PUT /settings/password ----------------------------------------------------------------


def _set_password(engine: Engine, *, username: str, password: str) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(username=username, password_hash=hash_password(password))
        )
        conn.commit()


def test_change_password_succeeds_with_correct_current_password(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _set_password(engine, username="jerome", password="old-password")
    r = client.put(
        "/api/v1/settings/password",
        json={"current_password": "old-password", "new_password": "new-password-123"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}

    login = client.post(
        "/api/v1/auth/login", json={"username": "jerome", "password": "new-password-123"}
    )
    assert login.status_code == 200


def test_change_password_rejects_wrong_current_password(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _set_password(engine, username="jerome", password="old-password")
    r = client.put(
        "/api/v1/settings/password",
        json={"current_password": "wrong", "new_password": "new-password-123"},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_change_password_rejects_a_too_short_new_password(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _set_password(engine, username="jerome", password="old-password")
    r = client.put(
        "/api/v1/settings/password",
        json={"current_password": "old-password", "new_password": "short"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_change_password_allows_a_first_time_set_with_no_existing_password(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # A username with no password_hash yet (e.g. an athlete only ever using a standing API key)
    # -- nothing to verify the "current" password against, so this must succeed regardless of
    # what current_password is sent.
    with engine.connect() as conn:
        conn.execute(
            athlete.update().where(athlete.c.id == DEFAULT_ATHLETE_ID).values(username="jerome")
        )
        conn.commit()
    r = client.put(
        "/api/v1/settings/password",
        json={"current_password": "anything", "new_password": "new-password-123"},
        headers=auth_headers,
    )
    assert r.status_code == 200


def test_change_password_locks_out_after_max_failed_attempts(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _set_password(engine, username="jerome", password="old-password")
    for _ in range(MAX_FAILED_ATTEMPTS):
        r = client.put(
            "/api/v1/settings/password",
            json={"current_password": "wrong", "new_password": "new-password-123"},
            headers=auth_headers,
        )
        assert r.status_code == 400

    # The next attempt is locked out even with the CORRECT current password.
    r = client.put(
        "/api/v1/settings/password",
        json={"current_password": "old-password", "new_password": "new-password-123"},
        headers=auth_headers,
    )
    assert r.status_code == 401


def test_change_password_requires_auth(client: TestClient) -> None:
    r = client.put(
        "/api/v1/settings/password",
        json={"current_password": "x", "new_password": "new-password-123"},
    )
    assert r.status_code in (401, 403)


# --- GET/POST /settings/eufy/status,/login --------------------------------------------------


def test_eufy_status_not_configured_by_default(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/settings/eufy/status", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"configured": False, "email": None}


def test_eufy_login_success_stores_credentials_and_updates_status(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(EufyClient, "login", lambda self: None)
    r = client.post(
        "/api/v1/settings/eufy/login",
        json={
            "email": "me@example.com",
            "password": "hunter2",
            "device_id": "dev-1",
            "customer_id": "cust-1",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}

    with engine.connect() as conn:
        row = conn.execute(select(athlete_eufy_config)).fetchone()
    assert row is not None
    assert (row.email, row.password, row.device_id, row.customer_id) == (
        "me@example.com",
        "hunter2",
        "dev-1",
        "cust-1",
    )

    status = client.get("/api/v1/settings/eufy/status", headers=auth_headers)
    assert status.json() == {"configured": True, "email": "me@example.com"}


def test_eufy_login_upserts_a_second_time_rather_than_duplicating(
    client: TestClient,
    auth_headers: dict[str, str],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(EufyClient, "login", lambda self: None)
    for device_id in ("dev-1", "dev-2"):
        client.post(
            "/api/v1/settings/eufy/login",
            json={
                "email": "me@example.com",
                "password": "hunter2",
                "device_id": device_id,
                "customer_id": "cust-1",
            },
            headers=auth_headers,
        )
    with engine.connect() as conn:
        rows = conn.execute(select(athlete_eufy_config)).fetchall()
    assert len(rows) == 1
    assert rows[0].device_id == "dev-2"


def test_eufy_login_wrong_credentials_returns_400_not_401(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_auth_error(self: object) -> None:
        raise EufyAuthError("bad credentials")

    monkeypatch.setattr(EufyClient, "login", raise_auth_error)
    r = client.post(
        "/api/v1/settings/eufy/login",
        json={
            "email": "me@example.com",
            "password": "wrong",
            "device_id": "dev-1",
            "customer_id": "cust-1",
        },
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_eufy_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/settings/eufy/status").status_code in (401, 403)
    assert client.post("/api/v1/settings/eufy/login", json={}).status_code in (401, 403)


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
    tokenstore_dir = test_settings.garmin_tokenstore_dir_for(DEFAULT_ATHLETE_ID)
    tokenstore_dir.mkdir(parents=True)
    (tokenstore_dir / "token.json").write_text("{}")

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


# --- POST /settings/rebuild -- launches `sync rebuild --tracked` as a standalone subprocess
# (see post_rebuild's own docstring for why: a real rebuild once hung for hours sharing a
# connection with this same live multi-worker api process). `subprocess.Popen` is mocked here so
# the test proves the endpoint delegates with the right argv, without actually spawning a real
# rebuild -- rebuild_database_tracked's own ingest_run bookkeeping is already covered end-to-end
# by tests/test_rebuild.py, so re-proving it here would just duplicate that coverage across a
# process boundary this test suite has no business crossing. -----------------------------------


def test_rebuild_trigger_launches_a_tracked_rebuild_subprocess(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kw: object) -> None:
            calls.append(argv)

    # settings.py does `import subprocess` (the whole module) and calls `subprocess.Popen`, so
    # patching the real subprocess module's own attribute -- not reaching through
    # settings_router's re-export of it -- is what actually takes effect, and is also what mypy's
    # no-implicit-reexport rule wants (module attributes aren't part of a module's public API).
    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    r = client.post("/api/v1/settings/rebuild", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"triggered": True}

    assert len(calls) == 1
    argv = calls[0]
    assert argv[1:] == [
        "-m",
        "perseverer.cli",
        "rebuild",
        "--tracked",
        "--athlete-id",
        DEFAULT_ATHLETE_ID,
    ]


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
