"""Tests for GET /insights -- a plain read against the insight table (Phase 8, ADR 0012)."""

from __future__ import annotations

import datetime as dt
import json

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from sporthealth.db.schema import insight
from sporthealth.db.seed import DEFAULT_ATHLETE_ID


def _seed_insight(
    engine: Engine, *, kind: str = "effort", window: str = "30d", subject_key: str = "distance:run"
) -> None:
    with engine.connect() as conn:
        conn.execute(
            insight.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                kind=kind,
                window=window,
                subject_key=subject_key,
                title="Longest distance (run)",
                detail=json.dumps({"activity_id": "a1"}),
                value_num=10000.0,
                activity_id=None,
                local_date="2026-08-10",
                computed_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
            )
        )
        conn.commit()


def test_list_insights_returns_seeded_rows(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_insight(engine)
    r = client.get("/api/v1/insights", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["title"] == "Longest distance (run)"
    assert body[0]["detail"] == {"activity_id": "a1"}


def test_filter_by_kind(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_insight(engine, kind="effort", subject_key="distance:run")
    _seed_insight(engine, kind="streak", subject_key="streak:current")
    r = client.get("/api/v1/insights?kind=streak", headers=auth_headers)
    body = r.json()
    assert len(body) == 1
    assert body[0]["kind"] == "streak"


def test_filter_by_window(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_insight(engine, window="30d", subject_key="distance:run")
    _seed_insight(engine, window="90d", subject_key="distance:run")
    r = client.get("/api/v1/insights?window=90d", headers=auth_headers)
    body = r.json()
    assert len(body) == 1
    assert body[0]["window"] == "90d"


def test_no_insights_returns_empty_list(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.get("/api/v1/insights", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []
