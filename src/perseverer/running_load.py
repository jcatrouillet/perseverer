"""Pace-based running TSS-equivalent ("rTSS"), calibrated to an athlete's own configured
threshold pace -- the same Coggan convention TrainingPeaks and intervals.icu already use for
running (100 = exactly one hour spent at threshold pace), computed here so `fitness.py`'s
CTL/ATL/TSB has an input for running that sits on that familiar scale instead of Garmin's own
uncalibrated `fit.session.training_load_peak` (Firstbeat's proprietary EPOC/HR-based number --
see fitness.py's own module docstring for why the two were never meant to match).

Formula (same shape as power-based TSS with IF = Normalized Power / FTP, just in pace/speed
space):

    threshold_speed_mps = 1000 / threshold_pace_sec_per_km
    IF = avg_gap_speed_mps / threshold_speed_mps
    rTSS = (moving_duration_s / 3600) * IF^2 * 100

`avg_gap_speed_mps` is deliberately the grade-adjusted speed `gap.py::refresh_avg_gap` already
computes and stores per running activity (`AVG_GAP_METRIC_KEY`), not raw average speed -- the
same reasoning `vdot.py`/`performance.py` already apply: a route's terrain shouldn't inflate or
deflate the training-stress estimate purely because it was net uphill or downhill. This module
adds zero new stream/Parquet reads of its own; it only combines `activity_metric` rows
`refresh_avg_gap` already wrote with the activity's own `moving_duration_s` and one
athlete-configured constant.

`refresh_running_tss` is a full delete+reinsert per athlete on every call -- same idempotent-
recompute precedent as `refresh_vdot`/`refresh_pace_bands`/`refresh_avg_gap` (all called
alongside this one at every ingest entry point, in that dependency order -- this module's own
input requires `refresh_avg_gap` to have already run in the same pass). With no
`athlete_running_load_config` row, or a null `threshold_pace_sec_per_km` (not configured yet --
same "all-null means not configured" contract as `athlete_hr_zone_config`), this is a no-op that
clears any stale rows and returns 0: `fitness.py` then falls back to `training_load_peak` for
every activity, exactly matching today's behavior byte-for-byte.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Connection, select

from perseverer.db.schema import (
    activity,
    activity_metric,
    athlete_running_load_config,
)
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.metrics.registry import get_or_register_metric

_SOURCE = "perseverer"
RUNNING_TSS_METRIC_KEY = "perseverer.performance.running_tss"


def compute_running_tss(
    moving_duration_s: float | None,
    avg_gap_speed_mps: float | None,
    threshold_pace_sec_per_km: float | None,
) -> float | None:
    """rTSS for one run, or `None` if any input is missing/non-positive -- callers (here,
    `refresh_running_tss`) should skip writing a row rather than store a meaningless value, so
    `fitness.py`'s own fallback to `training_load_peak` kicks in for that activity."""
    if moving_duration_s is None or moving_duration_s <= 0:
        return None
    if avg_gap_speed_mps is None or avg_gap_speed_mps <= 0:
        return None
    if threshold_pace_sec_per_km is None or threshold_pace_sec_per_km <= 0:
        return None

    threshold_speed_mps = 1000.0 / threshold_pace_sec_per_km
    intensity_factor = avg_gap_speed_mps / threshold_speed_mps
    duration_hours = moving_duration_s / 3600.0
    return duration_hours * intensity_factor**2 * 100.0


def refresh_running_tss(conn: Connection, *, athlete_id: str) -> int:
    """Returns the number of activities a running_tss value was written for."""
    get_or_register_metric(
        conn,
        metric_key=RUNNING_TSS_METRIC_KEY,
        source=_SOURCE,
        display_name="Running TSS",
        unit_si=None,
        category="performance",
        value_type="numeric",
    )

    conn.execute(
        activity_metric.delete().where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == RUNNING_TSS_METRIC_KEY,
        )
    )

    threshold_pace_sec_per_km = conn.execute(
        select(athlete_running_load_config.c.threshold_pace_sec_per_km).where(
            athlete_running_load_config.c.athlete_id == athlete_id
        )
    ).scalar_one_or_none()
    if threshold_pace_sec_per_km is None:
        return 0

    avg_gap_by_activity_id = {
        row.activity_id: row.value_num
        for row in conn.execute(
            select(activity_metric.c.activity_id, activity_metric.c.value_num).where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.metric_key == AVG_GAP_METRIC_KEY,
            )
        )
    }

    rows = conn.execute(
        select(activity.c.id, activity.c.moving_duration_s).where(
            activity.c.athlete_id == athlete_id,
            activity.c.sport == "running",
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()

    now = datetime.now(UTC).replace(tzinfo=None)
    written = 0
    for row in rows:
        avg_gap_speed_mps = avg_gap_by_activity_id.get(row.id)
        rtss = compute_running_tss(
            row.moving_duration_s, avg_gap_speed_mps, threshold_pace_sec_per_km
        )
        if rtss is None:
            continue
        conn.execute(
            activity_metric.insert().values(
                athlete_id=athlete_id,
                activity_id=row.id,
                metric_key=RUNNING_TSS_METRIC_KEY,
                value_num=rtss,
                value_text=None,
                unit=None,
                source=_SOURCE,
                created_at=now,
            )
        )
        written += 1

    return written
