"""Tests for GET /insights/pace-bands -- a plain SUM/GROUP BY over already-precomputed
activity_metric rows (Insights "Training bands" chart), never a live stream scan."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import activity, activity_metric
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.metrics.registry import get_or_register_metric
from perseverer.pace_bands import PACE_BANDS


def _seed_activity(engine: Engine, *, activity_id: str, local_date: str = "2026-01-01") -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date=local_date,
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
            source="perseverer",
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
                source="perseverer",
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


def test_by_activity_returns_no_rows_with_no_data(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/insights/pace-bands/by-activity", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_by_activity_keeps_each_activity_own_breakdown_separate(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_activity(engine, activity_id="a1", local_date="2026-01-05")
    _seed_activity(engine, activity_id="a2", local_date="2026-02-10")
    _seed_pace_band(engine, activity_id="a1", suffix="5_00_5_30", seconds=600.0)
    _seed_pace_band(engine, activity_id="a1", suffix="walk", seconds=60.0)
    _seed_pace_band(engine, activity_id="a2", suffix="4_00_4_30", seconds=300.0)

    r = client.get("/api/v1/insights/pace-bands/by-activity", headers=auth_headers)
    body = r.json()
    assert len(body) == 2

    by_id = {row["activity_id"]: row for row in body}
    a1_bands = {b["label"]: b["seconds"] for b in by_id["a1"]["bands"]}
    a2_bands = {b["label"]: b["seconds"] for b in by_id["a2"]["bands"]}
    assert by_id["a1"]["local_date"] == "2026-01-05"
    assert a1_bands["5:00-5:30"] == 600.0
    assert a1_bands["Walk"] == 60.0
    assert a1_bands["4:00-4:30"] == 0.0
    assert by_id["a2"]["local_date"] == "2026-02-10"
    assert a2_bands["4:00-4:30"] == 300.0
    assert a2_bands["5:00-5:30"] == 0.0
    # Every band is always present, even ones this activity spent no time in.
    assert len(a1_bands) == len(PACE_BANDS)
    assert len(a2_bands) == len(PACE_BANDS)
