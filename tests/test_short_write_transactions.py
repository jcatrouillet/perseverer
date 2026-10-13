"""SQLite allows one writer at a time, and the API has to write too (OAuth token refreshes, notes,
settings). A sync must never hold the write lock while it waits on Garmin or reads stream files:
when it did, the MCP connector's token refresh failed with "database is locked" during the daily
sync. These tests check that no write transaction is open at those moments.

`sqlite3.Connection.in_transaction` is True only once a write has started (Python's sqlite3 opens
the transaction implicitly before the first INSERT/UPDATE/DELETE), which is exactly the lock that
blocks other writers; plain reads leave it False.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from sqlalchemy import Connection, Engine

from perseverer import gap, pace_bands, performance, run_pacing
from perseverer.adapters.garmin_connect import GarminConnectAdapter, RateLimiter
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_stream, athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.streams import write_activity_stream

START = dt.datetime(2026, 1, 1, 8, 0)


def _write_open(conn: Connection) -> bool:
    dbapi = conn.connection.dbapi_connection
    assert dbapi is not None
    return bool(dbapi.in_transaction)


def _engine_with_run(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    points = [
        StreamPoint(
            timestamp_utc=(START + dt.timedelta(seconds=i)).replace(tzinfo=dt.UTC),
            values={"distance_m": i * 3.0, "altitude_m": 50.0 + (i % 60) / 10, "speed_mps": 3.0},
        )
        for i in range(2200)  # 6.6 km at 3 m/s
    ]
    path, n_samples, channels = write_activity_stream(
        tmp_path / "parquet", DEFAULT_ATHLETE_ID, "run1", points
    )
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=START,
            )
        )
        conn.execute(
            activity.insert().values(
                id="run1",
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=START,
                utc_offset_s=0,
                local_date="2026-01-01",
                sport="running",
                duration_s=2200.0,
                moving_duration_s=2200.0,
                distance_m=6600.0,
                primary_source="fit_folder",
                created_at=START,
                updated_at=START,
            )
        )
        conn.execute(
            activity_stream.insert().values(
                activity_id="run1",
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=path,
                n_samples=n_samples,
                channels=json.dumps(channels),
            )
        )
        conn.commit()
    return engine


@pytest.mark.parametrize(
    ("module", "refresh"),
    [
        (performance, performance.refresh_vdot),
        (pace_bands, pace_bands.refresh_pace_bands),
        (gap, gap.refresh_avg_gap),
        (run_pacing, run_pacing.refresh_run_pacing),
    ],
)
def test_stream_recomputes_read_every_file_before_their_first_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    refresh: Callable[..., int],
) -> None:
    engine = _engine_with_run(tmp_path)
    reads: list[bool] = []
    with engine.connect() as conn:
        real_read = pq.read_table

        def read_table(*args: Any, **kwargs: Any) -> Any:
            reads.append(_write_open(conn))
            return real_read(*args, **kwargs)

        monkeypatch.setattr(module.pq, "read_table", read_table)
        written = refresh(conn, tmp_path / "parquet", athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

    assert reads, "the refresh should have read the run's stream"
    assert not any(reads), "a write transaction was open while a stream file was read"
    assert written >= 1


def test_activity_download_happens_before_any_write(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    fit_bytes = (Path(__file__).parent / "fixtures" / "fit" / "synthetic_run.fit").read_bytes()
    seen: list[bool] = []

    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=START,
            )
        )
        conn.commit()

        class Client:
            def login(self, tokenstore: str | None = None) -> tuple[None, None]:
                return None, None

            def download_activity(self, activity_id: str, dl_fmt: object = None) -> bytes:
                seen.append(_write_open(conn))
                return fit_bytes

        adapter = GarminConnectAdapter(
            tmp_path / "tokens", RateLimiter(0, 999), client_factory=Client
        )
        adapter._client = Client()
        adapter.fetch_and_ingest_activity(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_summary={"activityId": "42"},
        )
        conn.commit()

    assert seen == [False], "a write transaction was open during the Garmin download"
