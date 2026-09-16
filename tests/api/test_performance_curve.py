"""Tests for GET /performance/curve. The sliding-window algorithm itself is exhaustively covered
by tests/test_performance_curve.py -- these check the router's own responsibilities: query-param
handling (metric validation, `sports` comma-splitting, `available` shaping), and threading the
request through to `compute_performance_curve` correctly.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine

from perseverer.db.schema import activity, activity_stream, performance_daily_rollup
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.streams import write_activity_stream


def _seed_activity_with_stream(
    conn: Connection,
    parquet_dir: Path,
    *,
    activity_id: str,
    local_date: str,
    sport: str,
    start: dt.datetime,
    values: dict[str, list[float]],
) -> None:
    n = len(next(iter(values.values())))
    points = [
        StreamPoint(
            timestamp_utc=start + dt.timedelta(seconds=i),
            values={k: v[i] for k, v in values.items()},
        )
        for i in range(n)
    ]
    relative_path, n_samples, channels = write_activity_stream(
        parquet_dir, DEFAULT_ATHLETE_ID, activity_id, points
    )
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=0,
            local_date=local_date,
            sport=sport,
            duration_s=float(n - 1),
            primary_source="fit_folder",
            created_at=start,
            updated_at=start,
        )
    )
    conn.execute(
        activity_stream.insert().values(
            activity_id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            parquet_path=relative_path,
            n_samples=n_samples,
            channels=json.dumps(channels),
        )
    )
    conn.commit()


def test_requires_a_valid_metric(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get(
        "/api/v1/performance/curve",
        params={"metric": "power", "start_date": "2026-06-01", "end_date": "2026-06-30"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_available_false_when_nothing_qualifies(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/api/v1/performance/curve",
        params={"metric": "pace", "start_date": "2026-06-01", "end_date": "2026-06-30"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["points"] == []
    assert body["metric"] == "pace"


def test_returns_a_real_curve_for_heart_rate(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, tmp_path: Path
) -> None:
    start = dt.datetime(2026, 6, 1, 10, 0, tzinfo=dt.UTC)
    with engine.connect() as conn:
        _seed_activity_with_stream(
            conn, tmp_path / "parquet",
            activity_id="a1", local_date="2026-06-01", sport="running", start=start,
            values={"heart_rate": [150.0] * 30},
        )

    r = client.get(
        "/api/v1/performance/curve",
        params={
            "metric": "heart_rate", "start_date": "2026-06-01", "end_date": "2026-06-30",
            "sports": "running,cycling",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    point = next(p for p in body["points"] if p["duration_s"] == 15)
    assert point["value"] == 150.0
    assert point["activity_id"] == "a1"


def test_sports_param_is_comma_split_and_scopes_heart_rate(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, tmp_path: Path
) -> None:
    start = dt.datetime(2026, 6, 1, 10, 0, tzinfo=dt.UTC)
    with engine.connect() as conn:
        _seed_activity_with_stream(
            conn, tmp_path / "parquet",
            activity_id="ride1", local_date="2026-06-01", sport="cycling", start=start,
            values={"heart_rate": [160.0] * 30},
        )

    r = client.get(
        "/api/v1/performance/curve",
        params={
            "metric": "heart_rate", "start_date": "2026-06-01", "end_date": "2026-06-30",
            "sports": "running",  # cycling not included
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_pace_ignores_sports_param_and_stays_running_only(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, tmp_path: Path
) -> None:
    start = dt.datetime(2026, 6, 1, 10, 0, tzinfo=dt.UTC)
    with engine.connect() as conn:
        _seed_activity_with_stream(
            conn, tmp_path / "parquet",
            activity_id="ride1", local_date="2026-06-01", sport="cycling", start=start,
            values={"distance_m": [i * 10.0 for i in range(30)], "altitude_m": [100.0] * 30},
        )

    r = client.get(
        "/api/v1/performance/curve",
        params={
            "metric": "pace", "start_date": "2026-06-01", "end_date": "2026-06-30",
            "sports": "cycling",  # ignored for pace -- must not pick up the ride
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_reference_values_are_included_for_the_requested_metric(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, tmp_path: Path
) -> None:
    start = dt.datetime(2026, 6, 1, 10, 0, tzinfo=dt.UTC)
    with engine.connect() as conn:
        _seed_activity_with_stream(
            conn, tmp_path / "parquet",
            activity_id="a1", local_date="2026-06-01", sport="running", start=start,
            values={"distance_m": [i * 4.0 for i in range(30)], "altitude_m": [100.0] * 30},
        )
        today = dt.datetime.now(dt.UTC).date().isoformat()
        conn.execute(
            performance_daily_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date=today,
                threshold_pace_s_per_km=280.0,
                aerobic_threshold_pace_s_per_km=320.0,
                refreshed_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
            )
        )
        conn.commit()

    r = client.get(
        "/api/v1/performance/curve",
        params={"metric": "pace", "start_date": "2026-06-01", "end_date": "2026-06-30"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["threshold_pace_s_per_km"] == 280.0
    assert body["aerobic_threshold_pace_s_per_km"] == 320.0
    assert body["threshold_hr_bpm"] is None


def test_performance_curve_endpoint_requires_auth(client: TestClient) -> None:
    r = client.get(
        "/api/v1/performance/curve",
        params={"metric": "pace", "start_date": "2026-06-01", "end_date": "2026-06-30"},
    )
    assert r.status_code in (401, 403)
