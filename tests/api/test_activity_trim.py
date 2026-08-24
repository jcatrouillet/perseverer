"""Tests for POST/DELETE /activities/{id}/trim, and the transport_mix_flag/has_trim fields on
GET /activities/{id}. See src/perseverer/activity_trim.py and transport_mix.py for the
underlying logic these endpoints wrap.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.archive import archive_raw_bytes
from perseverer.config import Settings
from perseverer.db.schema import (
    activity,
    activity_source_link,
    activity_stream,
    activity_trim_override,
    lap,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.ingest_dispatch import FIT_KIND
from tests.api.conftest import seed_activity

ACTIVITY_ID = "act1"
ACTIVITY_START = dt.datetime(2025, 6, 1, 10, 0, 0)
FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_run.fit"


def _write_parquet(path: Path, *, n_samples: int = 200) -> None:
    timestamps = [ACTIVITY_START + dt.timedelta(seconds=i) for i in range(n_samples)]
    table = pa.table(
        {
            "timestamp_utc": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
            "lat": [37.0 + i * 0.0001 for i in range(n_samples)],
            "lon": [-122.0] * n_samples,
            "distance_m": [float(i * 2) for i in range(n_samples)],
            "altitude_m": [100.0 + i * 0.1 for i in range(n_samples)],
            "heart_rate": [float(100 + (i % 40)) for i in range(n_samples)],
            "speed_mps": [1.5] * n_samples,
        }
    )
    pq.write_table(table, path)


def _seed_trimmable_activity(engine: Engine, settings: Settings) -> None:
    with engine.connect() as conn:
        content = FIXTURE.read_bytes()
        raw_object_id = archive_raw_bytes(
            conn,
            settings.raw_archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            kind=FIT_KIND,
            content=content,
        )
        now = dt.datetime.now(dt.UTC)
        conn.execute(
            activity.insert().values(
                id=ACTIVITY_ID,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=ACTIVITY_START,
                utc_offset_s=0,
                local_date="2025-06-01",
                sport="hiking",
                sub_sport="generic",
                name="Test hike",
                duration_s=200.0,
                moving_duration_s=200.0,
                distance_m=400.0,
                elevation_gain_m=20.0,
                calories=300.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            activity_source_link.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=ACTIVITY_ID,
                source="fit_folder",
                external_id="test-external-id",
                raw_object_id=raw_object_id,
                ingested_at=now,
            )
        )
        parquet_path = settings.parquet_dir / f"{ACTIVITY_ID}.parquet"
        settings.parquet_dir.mkdir(parents=True, exist_ok=True)
        _write_parquet(parquet_path)
        conn.execute(
            activity_stream.insert().values(
                activity_id=ACTIVITY_ID,
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=parquet_path.name,
                n_samples=200,
                channels=(
                    '["lat", "lon", "distance_m", "altitude_m", "heart_rate", "speed_mps"]'
                ),
            )
        )
        conn.execute(
            lap.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=ACTIVITY_ID,
                lap_index=0,
                start_time_utc=ACTIVITY_START,
                duration_s=200.0,
                distance_m=400.0,
            )
        )
        conn.commit()


class TestGetActivityTransportMixFlag:
    def test_flags_a_hike_with_a_sustained_fast_tail(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        with engine.connect() as conn:
            content = FIXTURE.read_bytes()
            raw_object_id = archive_raw_bytes(
                conn,
                test_settings.raw_archive_dir,
                athlete_id=DEFAULT_ATHLETE_ID,
                source="fit_folder",
                kind=FIT_KIND,
                content=content,
            )
            now = dt.datetime.now(dt.UTC)
            conn.execute(
                activity.insert().values(
                    id=ACTIVITY_ID,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    start_time_utc=ACTIVITY_START,
                    utc_offset_s=0,
                    local_date="2025-06-01",
                    sport="hiking",
                    sub_sport="generic",
                    duration_s=2000.0,
                    distance_m=20000.0,
                    primary_source="fit_folder",
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                activity_source_link.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id=ACTIVITY_ID,
                    source="fit_folder",
                    external_id="test-external-id",
                    raw_object_id=raw_object_id,
                    ingested_at=now,
                )
            )
            test_settings.parquet_dir.mkdir(parents=True, exist_ok=True)
            parquet_path = test_settings.parquet_dir / f"{ACTIVITY_ID}.parquet"
            timestamps = [ACTIVITY_START + dt.timedelta(seconds=i) for i in range(2000)]
            speeds = [1.2] * 1700 + [15.0] * 300
            table = pa.table(
                {
                    "timestamp_utc": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
                    "speed_mps": speeds,
                }
            )
            pq.write_table(table, parquet_path)
            conn.execute(
                activity_stream.insert().values(
                    activity_id=ACTIVITY_ID,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    parquet_path=parquet_path.name,
                    n_samples=2000,
                    channels='["speed_mps"]',
                )
            )
            conn.commit()

        r = client.get(f"/api/v1/activities/{ACTIVITY_ID}", headers=auth_headers)
        assert r.status_code == 200
        flag = r.json()["transport_mix_flag"]
        assert flag is not None
        assert flag["at_end"] is True
        assert flag["at_start"] is False
        assert r.json()["has_trim"] is False

    def test_null_for_a_plain_run(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id=ACTIVITY_ID, sport="running")

        r = client.get(f"/api/v1/activities/{ACTIVITY_ID}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["transport_mix_flag"] is None
        assert r.json()["has_trim"] is False


def _seed_flagged_hike(engine: Engine, settings: Settings, *, activity_id: str) -> None:
    """Same fast-tail-speed pattern as TestGetActivityTransportMixFlag's own fixture, factored
    out so the list-endpoint tests can seed more than one flagged activity."""
    start = ACTIVITY_START + dt.timedelta(days=hash(activity_id) % 30)
    with engine.connect() as conn:
        content = FIXTURE.read_bytes()
        raw_object_id = archive_raw_bytes(
            conn,
            settings.raw_archive_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            kind=FIT_KIND,
            content=content,
        )
        now = dt.datetime.now(dt.UTC)
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=start,
                utc_offset_s=0,
                local_date=start.date().isoformat(),
                sport="hiking",
                sub_sport="generic",
                name="Test hike",
                duration_s=2000.0,
                distance_m=20000.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            activity_source_link.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                source="fit_folder",
                external_id=f"{activity_id}-ext",
                raw_object_id=raw_object_id,
                ingested_at=now,
            )
        )
        settings.parquet_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = settings.parquet_dir / f"{activity_id}.parquet"
        timestamps = [start + dt.timedelta(seconds=i) for i in range(2000)]
        speeds = [1.2] * 1700 + [15.0] * 300
        table = pa.table(
            {
                "timestamp_utc": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
                "speed_mps": speeds,
            }
        )
        pq.write_table(table, parquet_path)
        conn.execute(
            activity_stream.insert().values(
                activity_id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=parquet_path.name,
                n_samples=2000,
                channels='["speed_mps"]',
            )
        )
        conn.commit()


class TestListTrimCandidates:
    def test_lists_a_flagged_hike(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_flagged_hike(engine, test_settings, activity_id="flagged1")

        r = client.get("/api/v1/activities/needs-trim", headers=auth_headers)
        assert r.status_code == 200
        candidates = r.json()
        assert [c["id"] for c in candidates] == ["flagged1"]
        assert candidates[0]["flag"]["at_end"] is True

    def test_excludes_an_already_trimmed_activity(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_flagged_hike(engine, test_settings, activity_id="flagged2")
        r = client.post(
            "/api/v1/activities/flagged2/trim",
            json={"trim_end_s": 1700.0},
            headers=auth_headers,
        )
        assert r.status_code == 200

        r = client.get("/api/v1/activities/needs-trim", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []

    def test_excludes_a_plain_run(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_trimmable_activity(engine, test_settings)  # a hike with constant, non-flagging speed

        r = client.get("/api/v1/activities/needs-trim", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []


class TestPostActivityTrim:
    def test_commits_and_returns_updated_activity(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_trimmable_activity(engine, test_settings)

        r = client.post(
            f"/api/v1/activities/{ACTIVITY_ID}/trim",
            json={"trim_start_s": 50.0, "trim_end_s": 150.0},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["distance_m"] == pytest.approx(200.0)
        assert body["calories"] is None
        assert body["has_trim"] is True

        with engine.connect() as conn:
            override = conn.execute(
                select(activity_trim_override).where(
                    activity_trim_override.c.athlete_id == DEFAULT_ATHLETE_ID
                )
            ).fetchone()
        assert override is not None
        assert override.trim_start_s == 50.0
        assert override.trim_end_s == 150.0

    def test_422_when_neither_bound_given(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_trimmable_activity(engine, test_settings)

        r = client.post(
            f"/api/v1/activities/{ACTIVITY_ID}/trim", json={}, headers=auth_headers
        )
        assert r.status_code == 422

    def test_404_for_unknown_activity(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post(
            "/api/v1/activities/doesnotexist/trim",
            json={"trim_end_s": 100.0},
            headers=auth_headers,
        )
        assert r.status_code == 404


class TestDeleteActivityTrim:
    def test_restores_original_values(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_trimmable_activity(engine, test_settings)
        client.post(
            f"/api/v1/activities/{ACTIVITY_ID}/trim",
            json={"trim_start_s": 50.0, "trim_end_s": 150.0},
            headers=auth_headers,
        )

        r = client.delete(f"/api/v1/activities/{ACTIVITY_ID}/trim", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        # synthetic_run.fit's own real parsed values -- see test_activity_trim.py's module
        # docstring for the same ground truth used there.
        assert body["distance_m"] == pytest.approx(1500.0)
        assert body["duration_s"] == pytest.approx(600.0)
        assert body["calories"] == pytest.approx(80.0)
        assert body["has_trim"] is False

    def test_400_when_no_trim_active(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        engine: Engine,
        test_settings: Settings,
    ) -> None:
        _seed_trimmable_activity(engine, test_settings)

        r = client.delete(f"/api/v1/activities/{ACTIVITY_ID}/trim", headers=auth_headers)
        assert r.status_code == 400
