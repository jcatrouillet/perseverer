"""Tests for GET /health/stream -- the intraday health_stream/Parquet series, distinct from
GET /health/observations' once-or-a-few-per-day EAV rows. See
health/json_parser.py::parse_daily_body_battery_json for the one real producer today.
"""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.config import Settings
from perseverer.db.schema import health_stream, metric_definition
from perseverer.db.seed import DEFAULT_ATHLETE_ID

METRIC_KEY = "garmin.daily_body_battery.level"


def _seed_stream(
    engine: Engine,
    test_settings: Settings,
    *,
    year_month: str,
    points: list[tuple[dt.datetime, float]],
    source: str = "garmin_connect",
) -> None:
    with engine.connect() as conn:
        existing = conn.execute(
            metric_definition.select().where(metric_definition.c.metric_key == METRIC_KEY)
        ).fetchone()
        if existing is None:
            now = dt.datetime.now(dt.UTC)
            conn.execute(
                metric_definition.insert().values(
                    metric_key=METRIC_KEY,
                    display_name=METRIC_KEY,
                    category="health",
                    value_type="numeric",
                    first_seen_at=now,
                    first_seen_source=source,
                )
            )
        relative_path = f"{DEFAULT_ATHLETE_ID}/health/{METRIC_KEY}/{year_month}.parquet"
        full_path = test_settings.parquet_dir / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table(
            {
                "timestamp_utc": pa.array(
                    [ts for ts, _ in points], type=pa.timestamp("us", tz="UTC")
                ),
                "value": [v for _, v in points],
            }
        )
        pq.write_table(table, full_path)
        conn.execute(
            health_stream.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                metric_key=METRIC_KEY,
                year_month=year_month,
                parquet_path=relative_path,
                n_samples=len(points),
                source=source,
            )
        )
        conn.commit()


def test_returns_the_days_readings_in_order(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, test_settings: Settings
) -> None:
    _seed_stream(
        engine,
        test_settings,
        year_month="2026-08",
        points=[
            (dt.datetime(2026, 8, 23, 15, 0), 82.0),
            (dt.datetime(2026, 8, 23, 7, 0), 19.0),  # out of order on purpose
        ],
    )

    r = client.get(
        f"/api/v1/health/stream?metric_key={METRIC_KEY}&date=2026-08-23", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metric_key"] == METRIC_KEY
    assert body["local_date"] == "2026-08-23"
    assert body["values"] == [19.0, 82.0]  # sorted by timestamp
    assert body["timestamps"][0] < body["timestamps"][1]


def test_excludes_readings_from_a_different_day_in_the_same_month_file(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, test_settings: Settings
) -> None:
    _seed_stream(
        engine,
        test_settings,
        year_month="2026-08",
        points=[
            (dt.datetime(2026, 8, 23, 12, 0), 50.0),
            (dt.datetime(2026, 8, 24, 12, 0), 60.0),
        ],
    )

    r = client.get(
        f"/api/v1/health/stream?metric_key={METRIC_KEY}&date=2026-08-23", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["values"] == [50.0]


def test_empty_arrays_not_an_error_when_no_stream_exists(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        f"/api/v1/health/stream?metric_key={METRIC_KEY}&date=2026-08-23", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["timestamps"] == []
    assert body["values"] == []


def test_requires_metric_key_and_date(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/health/stream", headers=auth_headers)
    assert r.status_code == 422
