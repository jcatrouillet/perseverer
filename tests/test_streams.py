"""Tests for streams.py::write_health_stream's merge-across-calls behavior -- a real bug (found
while building the body-battery intraday feature): pyarrow round-trips a tz("UTC") Parquet column
back as tz-aware Python datetimes on read, but every `HealthStreamPoint` producer in this codebase
constructs naive-implicitly-UTC timestamps (this project's own DateTime convention). Merging
existing (tz-aware, post-readback) and new (naive) timestamps into one dict then sorting raised
`TypeError: can't compare offset-naive and offset-aware datetimes` on any *second* write to the
same (metric_key, year_month) file -- i.e. every day after the first a given month's stream file
was touched twice, a completely normal occurrence in production.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pyarrow.parquet as pq

from perseverer.health.types import HealthStreamPoint
from perseverer.streams import write_health_stream

METRIC_KEY = "test.metric"
ATHLETE_ID = "athlete1"
YEAR_MONTH = "2026-08"


def _point(hour: int, value: float) -> HealthStreamPoint:
    return HealthStreamPoint(
        metric_key=METRIC_KEY, timestamp_utc=dt.datetime(2026, 8, 1, hour), value=value
    )


def test_a_second_write_to_the_same_file_merges_with_the_first(tmp_path: Path) -> None:
    write_health_stream(tmp_path, ATHLETE_ID, METRIC_KEY, YEAR_MONTH, [_point(7, 19.0)])

    relative_path, n_samples = write_health_stream(
        tmp_path, ATHLETE_ID, METRIC_KEY, YEAR_MONTH, [_point(15, 82.0)]
    )

    assert n_samples == 2  # both points survive, not just the second write's own
    table = pq.read_table(tmp_path / relative_path)
    assert sorted(table.column("value").to_pylist()) == [19.0, 82.0]


def test_re_writing_the_same_timestamp_updates_the_value_not_duplicates_it(tmp_path: Path) -> None:
    write_health_stream(tmp_path, ATHLETE_ID, METRIC_KEY, YEAR_MONTH, [_point(7, 19.0)])
    _, n_samples = write_health_stream(
        tmp_path, ATHLETE_ID, METRIC_KEY, YEAR_MONTH, [_point(7, 21.0)]
    )

    assert n_samples == 1  # same instant -- updated in place, not a duplicate row


def test_mixing_naive_and_tz_aware_points_across_writes_does_not_raise(tmp_path: Path) -> None:
    """fit_parser.py's own HealthStreamPoint producers pass tz-aware timestamps;
    health/json_parser.py's pass naive ones -- both must merge cleanly into the same file."""
    naive_point = HealthStreamPoint(
        metric_key=METRIC_KEY, timestamp_utc=dt.datetime(2026, 8, 1, 7), value=19.0
    )
    aware_point = HealthStreamPoint(
        metric_key=METRIC_KEY,
        timestamp_utc=dt.datetime(2026, 8, 1, 15, tzinfo=dt.UTC),
        value=82.0,
    )
    write_health_stream(tmp_path, ATHLETE_ID, METRIC_KEY, YEAR_MONTH, [naive_point])
    _, n_samples = write_health_stream(tmp_path, ATHLETE_ID, METRIC_KEY, YEAR_MONTH, [aware_point])

    assert n_samples == 2
