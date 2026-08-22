"""Per-second (well, per-*sample* -- real device cadence, not a synthesized fixed grid) time-in-
pace-band, computed once per running activity from its own raw stream and stored as ordinary
`activity_metric` rows -- the same EAV mechanism `performance.py` already uses for VDOT, one
metric_key per band rather than a new table. `source="perseverer"` distinguishes these from
anything a vendor reported.

This exists because a whole-activity *average* pace (what `activity.distance_m` /
`activity.moving_duration_s` gives you, and what an earlier version of the Insights "Training
bands" chart used client-side) hides all the within-run variation a real training-bands view is
supposed to show -- an interval session with fast reps and slow recovery jogging has the same
average pace as a flat steady tempo run, but a completely different time-in-band profile. Getting
that right needs every sample's own instantaneous speed, which only the per-activity Parquet
stream has.

Full recompute on every call, same precedent as `performance.py::refresh_vdot` (called alongside
this at every ingest entry point): delete every pace-band row for the athlete, then reinsert from
every current `sport == "running"` activity that has a stream with a `speed_mps` channel. Keeps
these correct after a sport correction or a retroactive raw-data change, without tracking which
activities changed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
from sqlalchemy import Connection, select

from perseverer.db.schema import activity, activity_metric, activity_stream
from perseverer.metrics.registry import get_or_register_metric

_SOURCE = "perseverer"
_METRIC_KEY_PREFIX = "perseverer.performance.pace_band."

# Below this speed, a sample is treated as stopped (traffic light, tying a shoe), not a genuinely
# slow pace -- matches the frontend's own streamSpeedValue threshold (runningStats.ts) exactly, so
# the two layers agree on what counts as "moving" even though they compute different things from
# it. Excluded entirely, not bucketed into the slowest band: a paused runner isn't running slowly,
# they're not running.
_STATIONARY_MPS_FLOOR = 0.3

# Caps how much of one sample-to-sample gap gets attributed to a single band -- real device data
# is close to 1Hz, so a gap this size only ever comes from a GPS signal-loss/reception gap, not
# normal sampling. Undercounting a few seconds of a rare gap is safer than attributing a much
# longer one wholesale to whatever pace happened to be reported right before it.
_MAX_SAMPLE_GAP_S = 10.0


@dataclass(frozen=True)
class PaceBand:
    suffix: str
    label: str
    # Seconds/km. min is inclusive, max is exclusive; None means unbounded on that side.
    min_sec_per_km: float | None
    max_sec_per_km: float | None

    @property
    def metric_key(self) -> str:
        return f"{_METRIC_KEY_PREFIX}{self.suffix}"


# Fast to slow, 30-second/km-wide bands -- this app's own min/km pace convention (see
# frontend/src/runningStats.ts::formatMinPerKm), not a min/mile scale. The slowest explicit band's
# upper edge (8:30/km, 510s) is the same real-data-calibrated cutoff runningStats.ts's own
# IMPLAUSIBLE_RUN_PACE_MIN_PER_KM uses for "this isn't really running pace" -- duplicated here
# (backend Python, no shared code with the TS frontend) rather than a fabricated second threshold,
# same precedent as rules_pb.py/personalRecords already duplicating logic cross-language.
PACE_BANDS: tuple[PaceBand, ...] = (
    PaceBand("lt_3_30", "< 3:30", None, 210),
    PaceBand("3_30_4_00", "3:30-4:00", 210, 240),
    PaceBand("4_00_4_30", "4:00-4:30", 240, 270),
    PaceBand("4_30_5_00", "4:30-5:00", 270, 300),
    PaceBand("5_00_5_30", "5:00-5:30", 300, 330),
    PaceBand("5_30_6_00", "5:30-6:00", 330, 360),
    PaceBand("6_00_6_30", "6:00-6:30", 360, 390),
    PaceBand("6_30_7_00", "6:30-7:00", 390, 420),
    PaceBand("7_00_7_30", "7:00-7:30", 420, 450),
    PaceBand("7_30_8_00", "7:30-8:00", 450, 480),
    PaceBand("8_00_8_30", "8:00-8:30", 480, 510),
    PaceBand("walk", "Walk", 510, None),
)


def _band_for(sec_per_km: float) -> PaceBand:
    for band in PACE_BANDS:
        if (band.min_sec_per_km is None or sec_per_km >= band.min_sec_per_km) and (
            band.max_sec_per_km is None or sec_per_km < band.max_sec_per_km
        ):
            return band
    return PACE_BANDS[-1]  # unreachable: the band list is exhaustive over (0, inf)


def compute_pace_band_seconds(
    timestamps_utc: Sequence[datetime], speeds_mps: Sequence[float | None]
) -> dict[str, float]:
    """Seconds spent in each `PaceBand.metric_key`, from one activity's own raw stream samples.

    Each sample's speed is held constant for the interval to the *next* sample (right-open, i.e.
    trailing) -- an honest approximation given real stream data is a sequence of instants, not
    intervals, and matches the same convention `stream_query.py::downsample`'s bucket-averaging
    already uses for "which interval does this sample represent". `timestamps_utc` and
    `speeds_mps` must be the same length and share index order (as read straight off one Parquet
    table's columns).
    """
    totals: dict[str, float] = {band.metric_key: 0.0 for band in PACE_BANDS}
    for i in range(len(timestamps_utc) - 1):
        speed = speeds_mps[i]
        if speed is None or speed < _STATIONARY_MPS_FLOOR:
            continue
        dt_s = (timestamps_utc[i + 1] - timestamps_utc[i]).total_seconds()
        if dt_s <= 0:
            continue
        dt_s = min(dt_s, _MAX_SAMPLE_GAP_S)
        sec_per_km = 1000.0 / speed
        band = _band_for(sec_per_km)
        totals[band.metric_key] += dt_s
    return totals


def refresh_pace_bands(conn: Connection, parquet_dir: Path, *, athlete_id: str) -> int:
    """Returns the number of (activity, band) rows written -- not the number of activities, since
    one activity can contribute to several bands."""
    for band in PACE_BANDS:
        get_or_register_metric(
            conn,
            metric_key=band.metric_key,
            source=_SOURCE,
            display_name=f"Pace band: {band.label}",
            unit_si="s",
            category="performance",
            value_type="numeric",
        )

    band_metric_keys = [band.metric_key for band in PACE_BANDS]
    conn.execute(
        activity_metric.delete().where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key.in_(band_metric_keys),
        )
    )

    streams_by_activity_id = {
        row.activity_id: row
        for row in conn.execute(
            select(
                activity_stream.c.activity_id,
                activity_stream.c.parquet_path,
                activity_stream.c.channels,
            ).where(activity_stream.c.athlete_id == athlete_id)
        )
    }

    activity_ids = (
        conn.execute(
            select(activity.c.id).where(
                activity.c.athlete_id == athlete_id,
                activity.c.sport == "running",
                activity.c.deleted_at.is_(None),
            )
        )
        .scalars()
        .all()
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    written = 0
    for activity_id in activity_ids:
        stream_row = streams_by_activity_id.get(activity_id)
        if stream_row is None:
            continue
        channels = json.loads(stream_row.channels)
        if "speed_mps" not in channels:
            continue
        full_path = parquet_dir / stream_row.parquet_path
        if not full_path.exists():
            continue

        table = pq.read_table(full_path, columns=["timestamp_utc", "speed_mps"])
        timestamps = table.column("timestamp_utc").to_pylist()
        speeds = table.column("speed_mps").to_pylist()
        if len(timestamps) < 2:
            continue

        totals = compute_pace_band_seconds(timestamps, speeds)
        for band in PACE_BANDS:
            seconds = totals[band.metric_key]
            if seconds <= 0:
                continue
            conn.execute(
                activity_metric.insert().values(
                    athlete_id=athlete_id,
                    activity_id=activity_id,
                    metric_key=band.metric_key,
                    value_num=seconds,
                    value_text=None,
                    unit="s",
                    source=_SOURCE,
                    created_at=now,
                )
            )
            written += 1

    return written
