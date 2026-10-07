"""Tests for stream_query.downsample against an in-test synthetic Parquet fixture. See
docs/ARCHITECTURE.md.
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
    SQL-identifier-injection-shaped bug -- see docs/ARCHITECTURE.md."""
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


def test_window_filters_to_the_given_elapsed_range(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """window=(lo, hi) restricts to elapsed seconds from the file's own first sample -- the
    mechanism a trimmed activity's /stream endpoint relies on to never serve the trimmed-away
    portion, without the Parquet file itself ever being truncated (activity_trim.py)."""
    path = tmp_path / "stream.parquet"
    _write_fixture(path, n_samples=100)

    result = downsample(
        con,
        path,
        tier="low",  # 100 samples, well under the 200-point low-tier target: no bucketing
        channels=["heart_rate"],
        available_channels=frozenset({"heart_rate", "cadence"}),
        duration_s=99.0,
        n_samples=100,
        window=(10.0, 20.0),
    )

    assert len(result.timestamps) == 11  # seconds 10..20 inclusive
    assert result.timestamps[0] == datetime(2025, 6, 1, tzinfo=UTC) + timedelta(seconds=10)
    assert result.timestamps[-1] == datetime(2025, 6, 1, tzinfo=UTC) + timedelta(seconds=20)


def test_window_filters_when_bucketing_too(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "stream.parquet"
    n_samples = 5000
    _write_fixture(path, n_samples)

    result = downsample(
        con,
        path,
        tier="low",  # target 200, well below n_samples: exercises the bucketed branch
        channels=["heart_rate"],
        available_channels=frozenset({"heart_rate", "cadence"}),
        duration_s=1000.0,
        n_samples=n_samples,
        window=(0.0, 999.0),  # first 1000 of the 5000 seconds
    )

    assert result.timestamps[0] == datetime(2025, 6, 1, tzinfo=UTC)
    assert result.timestamps[-1] < datetime(2025, 6, 1, tzinfo=UTC) + timedelta(seconds=1000)


def test_window_bucket_width_uses_the_windows_own_span_not_duration_s(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The real gap this fixes: a caller asking for a 3-minute window inside a much longer
    activity, at a tier whose target is smaller than the *whole activity's* sample count, must
    still get that window at its own true resolution -- sizing the bucket width from
    `duration_s` (the whole 5000-second activity) instead of the window's own 180-second span
    would produce a bucket width of ceil(5000/200)=25s, collapsing this 180-second window into
    about 7 buckets instead of the ~180 one-second buckets it should get."""
    path = tmp_path / "stream.parquet"
    n_samples = 5000
    _write_fixture(path, n_samples)

    result = downsample(
        con,
        path,
        tier="low",  # target 200 -- below the whole file's 5000 samples, so bucketing applies
        channels=["heart_rate"],
        available_channels=frozenset({"heart_rate", "cadence"}),
        duration_s=5000.0,  # the whole activity's own span -- must NOT drive the bucket width
        n_samples=n_samples,
        window=(0.0, 179.0),  # a 180-second window, well under the tier's 200-point target
    )

    # ceil(180 / 200) == 1: one-second buckets, not the ~25s buckets duration_s=5000 would imply.
    assert len(result.timestamps) > 100


def test_window_with_unbounded_end_sizes_buckets_from_duration_s_minus_start(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """An unbounded window end (float("inf"), e.g. an untrimmed trim_end_s) has no span of its
    own to size buckets from -- falls back to `duration_s - window[0]`, treating `duration_s` as
    the activity's own full elapsed span."""
    path = tmp_path / "stream.parquet"
    n_samples = 5000
    _write_fixture(path, n_samples)

    result = downsample(
        con,
        path,
        tier="low",  # target 200
        channels=["heart_rate"],
        available_channels=frozenset({"heart_rate", "cadence"}),
        duration_s=5000.0,
        n_samples=n_samples,
        window=(4000.0, float("inf")),  # last 1000 seconds, open-ended
    )

    # ceil((5000 - 4000) / 200) == 5: five-second buckets over the remaining ~1000s -> ~200 points.
    assert 150 <= len(result.timestamps) <= 220
    assert result.timestamps[0] == datetime(2025, 6, 1, tzinfo=UTC) + timedelta(seconds=4000)
