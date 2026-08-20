"""Tests for GET /insights/pace-bands -- a plain SUM/GROUP BY over already-precomputed
activity_metric rows (Insights "Training bands" chart), never a live stream scan."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from sporthealth.db.schema import activity, activity_metric
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.metrics.registry import get_or_register_metric
from sporthealth.pace_bands import PACE_BANDS


def _seed_activity(engine: Engine, *, activity_id: str) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date="2026-01-01",
                sport="running",
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()


def _seed_pace_band(engine: Engine, *, activity_id: str, suffix: str, seconds: float) -> None:
    band = next(b for b in PACE_BANDS if b.suffix == suffix)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        get_or_register_metric(
            conn,
            metric_key=band.metric_key,
            source="sporthealth",
            display_name=f"Pace band: {band.label}",
            unit_si="s",
            category="performance",
            value_type="numeric",
        )
        conn.execute(
            activity_metric.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                metric_key=band.metric_key,
                value_num=seconds,
                value_text=None,
                unit="s",
                source="sporthealth",
                created_at=now,
            )
        )
        conn.commit()


def test_returns_every_band_even_with_no_data(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/insights/pace-bands", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == len(PACE_BANDS)
    assert all(row["seconds"] == 0 for row in body)
    assert [row["label"] for row in body] == [b.label for b in PACE_BANDS]


def test_sums_across_activities_for_the_same_band(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_activity(engine, activity_id="a1")
    _seed_activity(engine, activity_id="a2")
    _seed_pace_band(engine, activity_id="a1", suffix="5_00_5_30", seconds=600.0)
    _seed_pace_band(engine, activity_id="a2", suffix="5_00_5_30", seconds=300.0)
    _seed_pace_band(engine, activity_id="a2", suffix="walk", seconds=120.0)

    r = client.get("/api/v1/insights/pace-bands", headers=auth_headers)
    body = {row["label"]: row["seconds"] for row in r.json()}
    assert body["5:00-5:30"] == 900.0
    assert body["Walk"] == 120.0
    assert body["< 3:30"] == 0.0
