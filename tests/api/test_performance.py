"""Tests for GET /performance -- reads performance_daily_rollup directly (seeded here, isolating
the API-layer test from the rollup computation itself, which tests/test_performance_rollup.py
already covers).
"""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import performance_daily_rollup
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def test_performance_returns_rows_in_range_ordered_by_date_with_full_field_round_trip(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            performance_daily_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-02",
                rolling_vdot=50.0,
                max_hr_bpm=185.0,
                threshold_pace_s_per_km=255.1,
                threshold_hr_bpm=167.0,
                threshold_hr_source="empirical",
                predicted_5k_s=1196.0,
                predicted_10k_s=2479.0,
                predicted_half_marathon_s=5491.0,
                predicted_marathon_s=11439.0,
                refreshed_at=now,
            )
        )
        conn.execute(
            performance_daily_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                rolling_vdot=None,
                max_hr_bpm=None,
                threshold_pace_s_per_km=None,
                threshold_hr_bpm=None,
                threshold_hr_source=None,
                predicted_5k_s=None,
                predicted_10k_s=None,
                predicted_half_marathon_s=None,
                predicted_marathon_s=None,
                refreshed_at=now,
            )
        )
        conn.execute(
            performance_daily_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-07-01",
                rolling_vdot=55.0,
                max_hr_bpm=188.0,
                threshold_pace_s_per_km=245.0,
                threshold_hr_bpm=165.0,
                threshold_hr_source="fallback",
                predicted_5k_s=1100.0,
                predicted_10k_s=2300.0,
                predicted_half_marathon_s=5100.0,
                predicted_marathon_s=10800.0,
                refreshed_at=now,
            )
        )
        conn.commit()

    r = client.get(
        "/api/v1/performance?start_date=2025-06-01&end_date=2025-06-30", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert [row["local_date"] for row in body] == ["2025-06-01", "2025-06-02"]

    empty_row = body[0]
    assert empty_row["rolling_vdot"] is None
    assert empty_row["threshold_hr_source"] is None

    full_row = body[1]
    assert full_row["rolling_vdot"] == 50.0
    assert full_row["max_hr_bpm"] == 185.0
    assert full_row["threshold_pace_s_per_km"] == 255.1
    assert full_row["threshold_hr_bpm"] == 167.0
    assert full_row["threshold_hr_source"] == "empirical"
    assert full_row["predicted_5k_s"] == 1196.0
    assert full_row["predicted_10k_s"] == 2479.0
    assert full_row["predicted_half_marathon_s"] == 5491.0
    assert full_row["predicted_marathon_s"] == 11439.0
