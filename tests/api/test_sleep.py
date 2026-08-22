"""Tests for GET /sleep."""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import sleep_session, sleep_stage
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def test_list_sleep_returns_sessions_with_nested_stages(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        result = conn.execute(
            sleep_session.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                start_time_utc=now,
                end_time_utc=now,
                total_sleep_s=25000.0,
                sleep_score=80.0,
                source="fit_folder",
            )
        )
        assert result.inserted_primary_key is not None
        session_id = result.inserted_primary_key[0]
        conn.execute(
            sleep_stage.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                sleep_session_id=session_id,
                stage="deep",
                start_time_utc=now,
                end_time_utc=now,
            )
        )
        conn.commit()

    r = client.get(
        "/api/v1/sleep?start_date=2025-05-25&end_date=2025-06-05", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["local_date"] == "2025-06-01"
    assert body[0]["total_sleep_s"] == 25000.0
    assert len(body[0]["stages"]) == 1
    assert body[0]["stages"][0]["stage"] == "deep"


def test_list_sleep_empty_range_returns_empty_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/api/v1/sleep?start_date=2020-01-01&end_date=2020-01-31", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() == []
