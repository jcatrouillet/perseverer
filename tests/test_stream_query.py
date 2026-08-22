"""Tests for stream_query.downsample against an in-test synthetic Parquet fixture. See
docs/adr/0006-phase-3-read-api-and-rollups.md decisions 3-5.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from perseverer.stream_query import downsample


def _write_fixture(path: Path, n_samples: int, *, heart_rate_value: float = 100.0) -> None:
    start = datetime(2025, 6, 1, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=i) for i in range(n_samples)]
    table = pa.table(
        {
            "timestamp_utc": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
            "heart_rate": [heart_rate_value] * n_samples,
            "cadence": [float(i % 10) for i in range(n_samples)],
        }
    )
    pq.write_table(table, path)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def test_below_target_returns_every_raw_point(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "stream.parquet"
    _write_fixture(path, n_samples=50)

    result = downsample(
        con,
        path,
        tier="low",  # target 200, well above 50 samples
        channels=[],
        available_channels=frozenset({"heart_rate", "cadence"}),
        duration_s=49.0,
        n_samples=50,
    )

    assert len(result.timestamps) == 50
    assert result.timestamps[0] == datetime(2025, 6, 1, tzinfo=UTC)


def test_above_target_buckets_down_to_roughly_the_target(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "stream.parquet"
    n_samples = 5000
    _write_fixture(path, n_samples=n_samples)

    result = downsample(
        con,
        path,
        tier="low",  # target 200
        channels=["heart_rate"],
        available_channels=frozenset({"heart_rate", "cadence"}),
        duration_s=float(n_samples - 1),
        n_samples=n_samples,
    )

    assert len(result.timestamps) < n_samples
    # bucket width = ceil(4999 / 200) = 25 -> ~200 buckets
    assert 150 <= len(result.timestamps) <= 220


def test_bucket_average_is_correct_on_a_constant_channel(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "stream.parquet"
    n_samples = 2000
    _write_fixture(path, n_samples=n_samples, heart_rate_value=142.0)

    result = downsample(
        con,
        path,
        tier="low",
        channels=["heart_rate"],
        available_channels=frozenset({"heart_rate", "cadence"}),
        duration_s=float(n_samples - 1),
        n_samples=n_samples,
    )

    assert all(v == 142.0 for v in result.series["heart_rate"])


def test_empty_channels_defaults_to_all_available(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "stream.parquet"
    _write_fixture(path, n_samples=10)

    result = downsample(
        con,
        path,
        tier="low",
        channels=[],
        available_channels=frozenset({"heart_rate", "cadence"}),
        duration_s=9.0,
        n_samples=10,
    )

    assert set(result.series.keys()) == {"heart_rate", "cadence"}


def test_unknown_tier_raises(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "stream.parquet"
    _write_fixture(path, n_samples=10)

    with pytest.raises(ValueError, match="unknown tier"):
        downsample(
            con,
            path,
            tier="bogus",
            channels=[],
            available_channels=frozenset({"heart_rate"}),
            duration_s=9.0,
            n_samples=10,
        )


def test_unrecognized_channel_raises(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """The channel-allowlist check that stands between a client-supplied query param and a
    SQL-identifier-injection-shaped bug -- see ADR 0006 decision 5."""
    path = tmp_path / "stream.parquet"
    _write_fixture(path, n_samples=10)

    with pytest.raises(ValueError, match="unrecognized channel"):
        downsample(
            con,
            path,
            tier="low",
            channels=["heart_rate; DROP TABLE foo"],
            available_channels=frozenset({"heart_rate"}),
            duration_s=9.0,
            n_samples=10,
        )
