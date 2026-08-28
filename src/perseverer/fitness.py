"""Fitness & Form (Phase 6): an independently-computed Banister/Coggan CTL (Fitness, 42-day
EWMA)/ATL (Fatigue, 7-day EWMA)/TSB (Form = yesterday's CTL - yesterday's ATL) model over daily
training load, displayed alongside (not required to match) Garmin's own precomputed
TrainingReadinessDTO/TrainingHistory signals -- Garmin's raw exports have no CTL/ATL/TSB triplet
at all (confirmed against the real ingested database), so there is nothing to reconcile against
directly. See docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.

Per-activity daily training load prefers `running_load.py`'s `RUNNING_TSS_METRIC_KEY` (a
pace-based rTSS calibrated to the athlete's own configured threshold pace, on the standard
"100 = one hour at threshold" Coggan scale TrainingPeaks/intervals.icu also use) when one exists,
falling back to Garmin's own uncalibrated `fit.session.training_load_peak` for every other
activity (any non-running sport, or a running activity from before a threshold pace was
configured). Investigation this session found Perseverer's CTL/TSB reading consistently
1.8x-3x higher than intervals.icu's for the same real training precisely because
`training_load_peak` (Firstbeat's proprietary EPOC/HR-based number) was the sole input and was
never calibrated to that scale -- the EWMA math itself was already correct.

`refresh_fitness_rollup` does a **full recompute of the athlete's entire history** on every
call, not an incremental "touched dates forward" scheme: the input is a handful-of-thousand-row
bulk query plus a pure-Python linear EWMA pass, cheap even on the DS1019+'s Celeron (the
platform's "never aggregate at request time" constraint is about serving, not bounded
ingest-time computation) -- and a retroactive correction to old training-load data invalidates
every subsequent day's EWMA forward from that point regardless, so an incremental scheme
wouldn't even save asymptotic work, just adds a watermark failure mode.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection, delete, select

from perseverer.db.schema import activity, activity_metric, fitness_daily_rollup
from perseverer.running_load import RUNNING_TSS_METRIC_KEY

_TRAINING_LOAD_METRIC_KEY = "fit.session.training_load_peak"
_CTL_TIME_CONSTANT_DAYS = 42
_ATL_TIME_CONSTANT_DAYS = 7


def _dedupe_activity_load(
    rows: list[tuple[str, str, str, str, float]],
) -> dict[str, float]:
    """`rows` is (activity_id, local_date, primary_source, metric_source, value_num) for every
    activity_metric row matching the training-load metric key. `activity_metric`'s unique
    constraint includes `source`, so the same activity can carry more than one row when it was
    ingested via two adapters and merged -- summing naively would double-count. Prefer the row
    whose source matches the activity's own `primary_source`; if that source has no row for
    this activity, fall back to the max value_num among whatever sources exist (deterministic,
    errs toward not silently zeroing out a real activity's load). Returns {activity_id: load}.
    """
    by_activity: dict[str, list[tuple[str, float]]] = {}
    primary_source_by_activity: dict[str, str] = {}
    for activity_id, _local_date, primary_source, metric_source, value_num in rows:
        by_activity.setdefault(activity_id, []).append((metric_source, value_num))
        primary_source_by_activity[activity_id] = primary_source

    result: dict[str, float] = {}
    for activity_id, source_values in by_activity.items():
        primary = primary_source_by_activity[activity_id]
        matching = [v for s, v in source_values if s == primary]
        result[activity_id] = matching[0] if matching else max(v for _, v in source_values)
    return result


def refresh_fitness_rollup(conn: Connection, *, athlete_id: str) -> None:
    """Recomputes the athlete's entire `fitness_daily_rollup` history from scratch -- delete
    then reinsert, the same idempotent-recompute model `refresh_daily_rollup` uses (a rollup is
    a cache, not raw data). Never calls `conn.commit()` -- caller controls the transaction.
    A no-op (deletes existing rows, inserts nothing) if the athlete has no activities at all.
    """
    now = datetime.now(UTC)

    rows = conn.execute(
        select(
            activity_metric.c.activity_id,
            activity.c.local_date,
            activity.c.primary_source,
            activity_metric.c.source,
            activity_metric.c.value_num,
        )
        .select_from(activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id))
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == _TRAINING_LOAD_METRIC_KEY,
            activity_metric.c.value_num.is_not(None),
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()

    # Pace-calibrated rTSS, overlaid on top of training_load_peak below wherever one exists --
    # always a single row per activity, source="perseverer", so no dedup needed here unlike
    # training_load_peak above. An activity with no rTSS row (non-running sport, or no threshold
    # pace configured yet) keeps its training_load_peak value untouched -- see module docstring.
    rtss_rows = conn.execute(
        select(
            activity_metric.c.activity_id,
            activity.c.local_date,
            activity_metric.c.value_num,
        )
        .select_from(activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id))
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == RUNNING_TSS_METRIC_KEY,
            activity_metric.c.value_num.is_not(None),
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()

    conn.execute(
        delete(fitness_daily_rollup).where(fitness_daily_rollup.c.athlete_id == athlete_id)
    )

    if not rows and not rtss_rows:
        return

    local_date_by_activity = {r.activity_id: r.local_date for r in rows}
    load_by_activity = _dedupe_activity_load(
        [
            (r.activity_id, r.local_date, r.primary_source, r.source, r.value_num)
            for r in rows
        ]
    )

    for r in rtss_rows:
        local_date_by_activity[r.activity_id] = r.local_date
        load_by_activity[r.activity_id] = r.value_num

    load_by_date: dict[str, float] = {}
    for activity_id, load in load_by_activity.items():
        local_date = local_date_by_activity[activity_id]
        load_by_date[local_date] = load_by_date.get(local_date, 0.0) + load

    start = date.fromisoformat(min(load_by_date))
    # today, UTC -- matches local_date's own convention (the UTC calendar date, not
    # offset-adjusted -- see day_rollup docstring), so the series keeps advancing on rest days
    # even with no new activity ingested.
    end = now.date()

    ctl = 0.0
    atl = 0.0
    ctl_alpha = 1 - math.exp(-1 / _CTL_TIME_CONSTANT_DAYS)
    atl_alpha = 1 - math.exp(-1 / _ATL_TIME_CONSTANT_DAYS)

    day = start
    rollup_rows = []
    while day <= end:
        iso = day.isoformat()
        load_today = load_by_date.get(iso, 0.0)
        tsb = ctl - atl  # form entering today, before today's session
        ctl = ctl + (load_today - ctl) * ctl_alpha
        atl = atl + (load_today - atl) * atl_alpha
        rollup_rows.append(
            {
                "athlete_id": athlete_id,
                "local_date": iso,
                "training_load": load_today,
                "ctl": ctl,
                "atl": atl,
                "tsb": tsb,
                "refreshed_at": now,
            }
        )
        day += timedelta(days=1)

    conn.execute(fitness_daily_rollup.insert(), rollup_rows)
