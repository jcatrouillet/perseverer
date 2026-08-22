"""Tests for MCP tools (api/mcp_server.py). Each tool is a plain async function tested by
direct call, not through the MCP wire protocol -- that's the SDK's own responsibility to get
right, not something this project needs to re-test. Reuses tests/api/conftest.py's
client/engine/duckdb_con/seed_activity fixtures unchanged -- see
docs/adr/0007-phase-4-mcp-server.md decision 6 for why that works.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.api.mcp_server import (
    create_note,
    get_activity,
    get_activity_stream,
    get_calendar,
    list_activities,
    list_health_observations,
    list_notes,
    list_sleep,
)
from perseverer.config import get_settings
from perseverer.db.schema import activity_stream, day_rollup, health_observation, metric_definition
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.streams import write_activity_stream
from tests.api.conftest import TEST_API_KEY, seed_activity


@pytest.fixture(autouse=True)
def _mcp_tool_api_key_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """mcp_server.py's `_call_api` reads `get_settings()` directly (not via FastAPI's DI), so
    it never sees the `client` fixture's `app.dependency_overrides`. Point the real
    env-backed settings at TEST_API_KEY too, so the tool's outgoing in-process request is
    accepted by the same server it's calling.
    """
    monkeypatch.setenv("PERSEVERER_API_KEY", TEST_API_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_list_activities_returns_seeded_data(client: TestClient, engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running")
        seed_activity(conn, activity_id="a2", sport="cycling")

    result = await list_activities(sport="cycling")
    assert result["total"] == 1
    assert result["items"][0]["id"] == "a2"


async def test_get_activity_returns_detail(client: TestClient, engine: Engine) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    result = await get_activity("a1")
    assert result["id"] == "a1"
    assert result["laps"] == []


async def test_get_activity_stream_forces_low_tier_regardless_of_resolution(
    client: TestClient, engine: Engine, tmp_path: Path
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    start = dt.datetime(2025, 6, 1, 10, tzinfo=dt.UTC)
    points = [
        StreamPoint(
            timestamp_utc=start + dt.timedelta(seconds=i), values={"heart_rate": 100.0 + i}
        )
        for i in range(5000)
    ]
    rel_path, n_samples, channels = write_activity_stream(
        tmp_path / "parquet", DEFAULT_ATHLETE_ID, "a1", points
    )
    with engine.connect() as conn:
        conn.execute(
            activity_stream.insert().values(
                activity_id="a1",
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=rel_path,
                n_samples=n_samples,
                channels=json.dumps(channels),
                sample_rate_hint=1.0,
            )
        )
        conn.commit()

    result = await get_activity_stream("a1")
    assert result["tier"] == "low"
    assert len(result["timestamps"]) < 5000


async def test_list_health_observations(client: TestClient, engine: Engine) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            metric_definition.insert().values(
                metric_key="resting_heart_rate",
                display_name="resting_heart_rate",
                category="health",
                value_type="numeric",
                first_seen_at=now,
                first_seen_source="fit_folder",
            )
        )
        conn.execute(
            health_observation.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                metric_key="resting_heart_rate",
                observed_at_utc=now,
                local_date="2025-06-01",
                aggregation="daily",
                value_num=48.0,
                source="fit_folder",
            )
        )
        conn.commit()

    result = await list_health_observations(
        metric_key=["resting_heart_rate"], start_date="2025-06-01", end_date="2025-06-01"
    )
    assert result["total"] == 1
    assert result["items"][0]["value_num"] == 48.0


async def test_list_sleep_empty_range(client: TestClient) -> None:
    result = await list_sleep(start_date="2020-01-01", end_date="2020-01-02")
    assert result == []


async def test_get_calendar_reads_rollup_tables(client: TestClient, engine: Engine) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            day_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                activity_count=1,
                refreshed_at=now,
            )
        )
        conn.commit()

    result = await get_calendar(start_date="2025-06-01", end_date="2025-06-01")
    assert len(result["days"]) == 1
    assert result["days"][0]["activity_count"] == 1


async def test_create_note_then_list_notes_round_trip(client: TestClient) -> None:
    created = await create_note(entity_type="day", entity_id="2025-06-01", body="felt great")
    assert created["body"] == "felt great"

    notes = await list_notes(entity_type="day", entity_id="2025-06-01")
    assert len(notes) == 1
    assert notes[0]["body"] == "felt great"


async def test_create_note_404_for_unknown_activity(client: TestClient) -> None:
    with pytest.raises(httpx.HTTPStatusError, match="404"):
        await create_note(entity_type="activity", entity_id="doesnotexist", body="x")
