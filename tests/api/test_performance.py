"""Tests for GET /performance -- reads performance_daily_rollup directly (seeded here, isolating
the API-layer test from the rollup computation itself, which tests/test_performance_rollup.py
already covers). Also GET /performance/vo2max-analysis -- the request-time factor-analysis
endpoint, isolating the route/schema wiring from the actual window logic
tests/test_vo2max_analysis.py already covers.
"""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine

from perseverer.db.schema import activity, activity_metric, performance_daily_rollup
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.metrics.registry import get_or_register_metric
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.performance_rollup import refresh_performance_rollup


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
                aerobic_threshold_pace_s_per_km=296.8,
                aerobic_threshold_hr_bpm=157.0,
                aerobic_threshold_hr_source="empirical",
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
    assert full_row["aerobic_threshold_pace_s_per_km"] == 296.8
    assert full_row["aerobic_threshold_hr_bpm"] == 157.0
    assert full_row["aerobic_threshold_hr_source"] == "empirical"
    assert full_row["predicted_5k_s"] == 1196.0
    assert full_row["predicted_10k_s"] == 2479.0
    assert full_row["predicted_half_marathon_s"] == 5491.0
    assert full_row["predicted_marathon_s"] == 11439.0


def _add_run_with_vdot(
    conn: Connection, *, activity_id: str, local_date: str, name: str, vdot: float
) -> None:
    now = dt.datetime(2025, 6, 1, 10, 0, 0)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date=local_date,
            name=name,
            sport="running",
            duration_s=1800.0,
            moving_duration_s=1700.0,
            distance_m=5000.0,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )
    get_or_register_metric(
        conn,
        metric_key=VDOT_METRIC_KEY,
        source="perseverer",
        display_name="VDOT",
        unit_si=None,
        category="performance",
        value_type="numeric",
    )
    conn.execute(
        activity_metric.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            metric_key=VDOT_METRIC_KEY,
            value_num=vdot,
            source="perseverer",
            created_at=now,
        )
    )
    conn.commit()


def test_vo2max_analysis_identifies_the_driving_activity_and_other_contributors(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        _add_run_with_vdot(
            conn, activity_id="easy", local_date="2025-06-01", name="Easy jog", vdot=40.0
        )
        _add_run_with_vdot(
            conn, activity_id="race", local_date="2025-06-05", name="5k race", vdot=55.0
        )

    r = client.get(
        "/api/v1/performance/vo2max-analysis?as_of=2025-06-10", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rolling_vdot"] == 55.0
    assert body["driving_activity"]["activity_id"] == "race"
    assert body["driving_activity"]["name"] == "5k race"
    assert [c["activity_id"] for c in body["other_contributors"]] == ["easy"]
    assert body["expires_on"] is not None
    assert body["missing"] == []


def test_vo2max_analysis_defaults_as_of_to_today_when_omitted(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.get("/api/v1/performance/vo2max-analysis", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == dt.datetime.now(dt.UTC).date().isoformat()
    assert body["driving_activity"] is None
    assert any("No qualifying run yet" in m for m in body["missing"])


def test_threshold_analysis_shares_the_vo2max_driving_activity_for_both_paces(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        _add_run_with_vdot(
            conn, activity_id="tempo", local_date="2025-06-05", name="Tempo run", vdot=50.0
        )
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

    r = client.get(
        "/api/v1/performance/threshold-analysis?as_of=2025-06-10", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["vo2max"]["driving_activity"]["activity_id"] == "tempo"
    assert body["anaerobic_threshold_pace_s_per_km"] is not None
    assert body["aerobic_threshold_pace_s_per_km"] is not None
    # Aerobic is always the slower (larger seconds/km) of the two at the same VDOT.
    assert body["aerobic_threshold_pace_s_per_km"] > body["anaerobic_threshold_pace_s_per_km"]
    assert body["anaerobic_threshold_hr"]["reference_pace_s_per_km"] == (
        body["anaerobic_threshold_pace_s_per_km"]
    )
    assert body["aerobic_threshold_hr"]["reference_pace_s_per_km"] == (
        body["aerobic_threshold_pace_s_per_km"]
    )


def test_threshold_analysis_defaults_as_of_to_today_when_omitted(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    r = client.get("/api/v1/performance/threshold-analysis", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == dt.datetime.now(dt.UTC).date().isoformat()
    assert body["vo2max"]["driving_activity"] is None
    assert body["anaerobic_threshold_pace_s_per_km"] is None
    assert body["anaerobic_threshold_hr"]["threshold_hr_bpm"] is None
