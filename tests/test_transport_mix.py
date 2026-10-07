"""Tests for transport_mix.py against synthetic Parquet fixtures shaped like the real activity
that motivated this feature (a hike with a sustained fast segment touching one boundary). See
docs/ARCHITECTURE.md and
transport_mix.py's own module docstring for the real-data numbers this mirrors.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from perseverer.transport_mix import detect_transport_mix


def _write_fixture(path: Path, speeds: list[float]) -> None:
    start = datetime(2025, 1, 20, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=i) for i in range(len(speeds))]
    table = pa.table(
        {
            "timestamp_utc": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
            "speed_mps": speeds,
        }
    )
    pq.write_table(table, path)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def _hike_speeds(n: int) -> list[float]:
    return [1.2] * n


def _drive_speeds(n: int) -> list[float]:
    return [15.0] * n


def test_none_for_ineligible_sport(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "stream.parquet"
    _write_fixture(path, _hike_speeds(1000) + _drive_speeds(1000))

    assert detect_transport_mix(con, path, sport="running") is None
    assert detect_transport_mix(con, path, sport="cycling") is None


def test_none_for_pure_hike(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "stream.parquet"
    _write_fixture(path, _hike_speeds(2000))

    assert detect_transport_mix(con, path, sport="hiking") is None


def test_none_for_too_short_activity(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "stream.parquet"
    _write_fixture(path, _drive_speeds(30))  # under the 90s sustained-window threshold

    assert detect_transport_mix(con, path, sport="hiking") is None


def test_flags_drive_at_end_and_suggests_the_boundary(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "stream.parquet"
    hike_n = 1700
    _write_fixture(path, _hike_speeds(hike_n) + _drive_speeds(2000))

    result = detect_transport_mix(con, path, sport="hiking")

    assert result is not None
    assert result.at_end is True
    assert result.at_start is False
    # The suggested cut is where the hike itself ends -- the last hike-speed second, not
    # shifted by the full sustained-detection window.
    assert result.suggested_trim_end_s == pytest.approx(hike_n - 1, abs=2)
    assert result.suggested_trim_start_s is None


def test_flags_drive_at_start_and_suggests_the_boundary(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "stream.parquet"
    drive_n = 1200
    _write_fixture(path, _drive_speeds(drive_n) + _hike_speeds(2000))

    result = detect_transport_mix(con, path, sport="walking")

    assert result is not None
    assert result.at_start is True
    assert result.at_end is False
    assert result.suggested_trim_start_s == pytest.approx(drive_n, abs=2)
    assert result.suggested_trim_end_s is None


def test_flags_both_boundaries(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "stream.parquet"
    _write_fixture(path, _drive_speeds(1000) + _hike_speeds(1500) + _drive_speeds(1000))

    result = detect_transport_mix(con, path, sport="hiking")

    assert result is not None
    assert result.at_start is True
    assert result.at_end is True


def test_ignores_a_brief_fast_blip_within_the_hike(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """A short downhill jog or a GPS speed spike shouldn't be mistaken for a car segment -- only
    a *sustained* (90s+) fast stretch counts."""
    path = tmp_path / "stream.parquet"
    speeds = _hike_speeds(500) + _drive_speeds(20) + _hike_speeds(500)
    _write_fixture(path, speeds)

    assert detect_transport_mix(con, path, sport="hiking") is None


def test_real_activity_fixture_shape(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Mirrors the real 01M0PY94N9J7XM1H9VPYBHME3T profile that motivated this feature: ~28
    genuine hiking minutes, then a sharp jump to highway speed for the rest of the recording."""
    path = tmp_path / "stream.parquet"
    hike_s = 1670
    drive_s = 3778
    _write_fixture(path, _hike_speeds(hike_s) + _drive_speeds(drive_s))

    result = detect_transport_mix(con, path, sport="hiking")

    assert result is not None
    assert result.at_end is True
    assert result.suggested_trim_end_s == pytest.approx(hike_s - 1, abs=2)
