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


def test_list_sleep_assigns_each_sessions_stages_correctly_not_cross_mixed(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """GET /sleep batch-fetches every returned session's stages in one query and groups them by
    sleep_session_id in Python (see sleep.py's own docstring on that change) -- this pins down
    that two different nights' stages never get attributed to the wrong session, which a batched
    fetch is exactly the kind of change that could get subtly wrong."""
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        session_ids = {}
        for local_date in ("2025-06-01", "2025-06-02", "2025-06-03"):
            result = conn.execute(
                sleep_session.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date=local_date,
                    start_time_utc=now,
                    end_time_utc=now,
                    total_sleep_s=25000.0,
                    sleep_score=80.0,
                    source="fit_folder",
                )
            )
            assert result.inserted_primary_key is not None
            session_ids[local_date] = result.inserted_primary_key[0]

        conn.execute(
            sleep_stage.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                sleep_session_id=session_ids["2025-06-01"],
                stage="deep",
                start_time_utc=now,
                end_time_utc=now,
            )
        )
        conn.execute(
            sleep_stage.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                sleep_session_id=session_ids["2025-06-02"],
                stage="rem",
                start_time_utc=now,
                end_time_utc=now,
            )
        )
        # 2025-06-03 deliberately gets no stages at all -- a session with an empty stage list is
        # a real, common case (e.g. a device that reported total sleep time but no stage detail).
        conn.commit()

    r = client.get(
        "/api/v1/sleep?start_date=2025-05-25&end_date=2025-06-05", headers=auth_headers
    )
    assert r.status_code == 200
    by_date = {s["local_date"]: s for s in r.json()}
    assert [st["stage"] for st in by_date["2025-06-01"]["stages"]] == ["deep"]
    assert [st["stage"] for st in by_date["2025-06-02"]["stages"]] == ["rem"]
    assert by_date["2025-06-03"]["stages"] == []


def test_list_sleep_empty_range_returns_empty_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/api/v1/sleep?start_date=2020-01-01&end_date=2020-01-31", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() == []


def test_list_sleep_dedupes_overlapping_sources_preferring_garmin_connect(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A date covered by both garmin_export's historical backfill and garmin_connect's rolling
    live sync gets two legitimate sleep_session rows (see sleep.py's _SOURCE_PRIORITY docstring)
    -- GET /sleep must collapse them to one per local_date, not leak both to the frontend."""
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            sleep_session.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                start_time_utc=now,
                end_time_utc=now,
                total_sleep_s=30240.0,
                sleep_score=58.0,
                source="garmin_export",
            )
        )
        conn.execute(
            sleep_session.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                start_time_utc=now,
                end_time_utc=now,
                total_sleep_s=26100.0,
                sleep_score=58.0,
                source="garmin_connect",
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
    assert body[0]["source"] == "garmin_connect"
    assert body[0]["total_sleep_s"] == 26100.0
