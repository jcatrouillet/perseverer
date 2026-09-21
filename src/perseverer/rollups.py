"""Precomputed daily and period (week/month) rollups — the platform's only sanctioned way for
calendar/dashboard views to read aggregate data. AGENTS.md, non-negotiable: "the Celeron cannot
aggregate a decade of activities per request — every dashboard/calendar/recap view reads a
`*_rollup` table refreshed on ingest, never scans at request time."

`refresh_daily_rollup` is called once per distinct `local_date` an ingest run actually touched
(never once per file/record — see each adapter's ingest loop), by every ingest entry point:
`fit_folder.import_from_folder`, `garmin_export.import_garmin_export`,
`garmin_connect.sync_garmin_connect`, `rebuild.rebuild_database`. See
docs/adr/0006-phase-3-read-api-and-rollups.md decisions 1 and 3.

`refresh_period_rollup` (Phase 6) is a rollup OF `day_rollup`/`health_metric_daily_rollup`, not
of raw tables — it reuses the daily grain's already-solved judgment calls (longest-sleep-session
-wins, etc.) and guarantees week/month totals stay structurally consistent with the day rows
shown next to them in the calendar grid. Called once per distinct `(period_type, period_start)`
touched, derived from the same `touched_dates` every ingest entry point already tracks. See
docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection, delete, func, select

from perseverer.db.schema import (
    activity,
    day_rollup,
    health_metric_daily_rollup,
    health_metric_period_rollup,
    health_observation,
    period_rollup,
    sleep_session,
)

PeriodType = str  # "week" | "month" -- not a Literal to keep the DB column a plain String

# (local_date, value_sum, value_min, value_max, value_last, n_observations)
PeriodHealthRow = tuple[str, float | None, float | None, float | None, float | None, int]


def week_start_monday(local_date: str) -> str:
    """The Monday on/before `local_date` — confirmed against the user's own Garmin Connect
    account, which weeks Monday-start (see ADR 0009)."""
    d = date.fromisoformat(local_date)
    return (d - timedelta(days=d.weekday())).isoformat()


def month_start(local_date: str) -> str:
    return date.fromisoformat(local_date).replace(day=1).isoformat()


def _period_end(period_type: PeriodType, period_start: str) -> str:
    start = date.fromisoformat(period_start)
    if period_type == "week":
        return (start + timedelta(days=6)).isoformat()
    if period_type == "month":
        last_day = calendar.monthrange(start.year, start.month)[1]
        return start.replace(day=last_day).isoformat()
    raise ValueError(f"unknown period_type: {period_type!r}")


def refresh_daily_and_period_rollups(
    conn: Connection, *, athlete_id: str, touched_dates: set[str]
) -> None:
    """The one call every ingest entry point makes: refreshes the daily rollup for each
    touched date, then derives the (at most a few hundred, even for a multi-year backfill)
    distinct weeks/months those dates fall in and refreshes each exactly once — never once per
    touched date, which would recompute the same week/month repeatedly. A no-op if
    `touched_dates` is empty. Never calls `conn.commit()` -- caller controls the transaction.
    """
    touched_periods: set[tuple[PeriodType, str]] = set()
    for local_date in touched_dates:
        refresh_daily_rollup(conn, athlete_id=athlete_id, local_date=local_date)
        touched_periods.add(("week", week_start_monday(local_date)))
        touched_periods.add(("month", month_start(local_date)))
    for period_type, period_start in touched_periods:
        refresh_period_rollup(
            conn, athlete_id=athlete_id, period_type=period_type, period_start=period_start
        )


def refresh_daily_rollup(conn: Connection, *, athlete_id: str, local_date: str) -> None:
    """Recomputes both rollup tables' rows for one (athlete_id, local_date) from scratch --
    delete then reinsert, the same idempotent-recompute model `rebuild.py` already uses for
    every other derived table (a rollup is a cache, not raw data -- "never destructive" doesn't
    apply here). Caller controls the transaction: this never calls `conn.commit()`, matching
    `ingest_canonical_batch`/`ingest_health_batch`'s existing convention.
    """
    now = datetime.now(UTC)

    conn.execute(
        delete(day_rollup).where(
            day_rollup.c.athlete_id == athlete_id, day_rollup.c.local_date == local_date
        )
    )
    conn.execute(
        delete(health_metric_daily_rollup).where(
            health_metric_daily_rollup.c.athlete_id == athlete_id,
            health_metric_daily_rollup.c.local_date == local_date,
        )
    )

    activity_agg = conn.execute(
        select(
            func.count(activity.c.id).label("activity_count"),
            func.sum(activity.c.duration_s).label("duration_s"),
            func.sum(activity.c.moving_duration_s).label("moving_duration_s"),
            func.sum(activity.c.distance_m).label("distance_m"),
            func.sum(activity.c.elevation_gain_m).label("elevation_gain_m"),
            func.sum(activity.c.calories).label("calories"),
        ).where(
            activity.c.athlete_id == athlete_id,
            activity.c.local_date == local_date,
            activity.c.deleted_at.is_(None),
        )
    ).one()

    sleep_rows = conn.execute(
        select(sleep_session.c.total_sleep_s, sleep_session.c.sleep_score).where(
            sleep_session.c.athlete_id == athlete_id,
            sleep_session.c.local_date == local_date,
        )
    ).fetchall()
    sleep_total_s: float | None = None
    sleep_score: float | None = None
    if sleep_rows:
        # Multiple sources for one night (e.g. fit_folder + garmin_export both saw it): the
        # longest-duration row wins -- a documented heuristic, not a merge algorithm.
        best = max(sleep_rows, key=lambda r: r.total_sleep_s or 0.0)
        sleep_total_s = best.total_sleep_s
        sleep_score = best.sleep_score

    conn.execute(
        day_rollup.insert().values(
            athlete_id=athlete_id,
            local_date=local_date,
            activity_count=activity_agg.activity_count,
            activity_duration_s=activity_agg.duration_s,
            activity_moving_duration_s=activity_agg.moving_duration_s,
            activity_distance_m=activity_agg.distance_m,
            activity_elevation_gain_m=activity_agg.elevation_gain_m,
            activity_calories=activity_agg.calories,
            sleep_total_s=sleep_total_s,
            sleep_score=sleep_score,
            refreshed_at=now,
        )
    )

    health_rows = conn.execute(
        select(
            health_observation.c.metric_key,
            health_observation.c.value_num,
            health_observation.c.observed_at_utc,
        ).where(
            health_observation.c.athlete_id == athlete_id,
            health_observation.c.local_date == local_date,
            health_observation.c.value_num.is_not(None),
        )
    ).fetchall()

    by_metric: dict[str, list[tuple[float, datetime]]] = defaultdict(list)
    for row in health_rows:
        by_metric[row.metric_key].append((row.value_num, row.observed_at_utc))

    for metric_key, values in by_metric.items():
        nums = [v for v, _ in values]
        value_last = max(values, key=lambda item: item[1])[0]
        conn.execute(
            health_metric_daily_rollup.insert().values(
                athlete_id=athlete_id,
                local_date=local_date,
                metric_key=metric_key,
                value_sum=sum(nums),
                value_avg=sum(nums) / len(nums),
                value_min=min(nums),
                value_max=max(nums),
                value_last=value_last,
                n_observations=len(nums),
                refreshed_at=now,
            )
        )


def refresh_period_rollup(
    conn: Connection, *, athlete_id: str, period_type: PeriodType, period_start: str
) -> None:
    """Recomputes both period-rollup tables' rows for one (athlete_id, period_type,
    period_start) from `day_rollup`/`health_metric_daily_rollup` -- delete then reinsert, same
    idempotent-recompute model as `refresh_daily_rollup`. A day is read at most ~31 times (a
    month) per call; callers de-duplicate touched periods before calling, same discipline as
    the daily rollup. Never calls `conn.commit()` -- caller controls the transaction.
    """
    now = datetime.now(UTC)
    period_end = _period_end(period_type, period_start)

    conn.execute(
        delete(period_rollup).where(
            period_rollup.c.athlete_id == athlete_id,
            period_rollup.c.period_type == period_type,
            period_rollup.c.period_start == period_start,
        )
    )
    conn.execute(
        delete(health_metric_period_rollup).where(
            health_metric_period_rollup.c.athlete_id == athlete_id,
            health_metric_period_rollup.c.period_type == period_type,
            health_metric_period_rollup.c.period_start == period_start,
        )
    )

    day_rows = conn.execute(
        select(day_rollup).where(
            day_rollup.c.athlete_id == athlete_id,
            day_rollup.c.local_date >= period_start,
            day_rollup.c.local_date <= period_end,
        )
    ).fetchall()

    def _sum_or_none(values: list[float | None]) -> float | None:
        present = [v for v in values if v is not None]
        return sum(present) if present else None

    conn.execute(
        period_rollup.insert().values(
            athlete_id=athlete_id,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            activity_count=sum(r.activity_count for r in day_rows),
            activity_duration_s=_sum_or_none([r.activity_duration_s for r in day_rows]),
            activity_moving_duration_s=_sum_or_none(
                [r.activity_moving_duration_s for r in day_rows]
            ),
            activity_distance_m=_sum_or_none([r.activity_distance_m for r in day_rows]),
            activity_elevation_gain_m=_sum_or_none(
                [r.activity_elevation_gain_m for r in day_rows]
            ),
            activity_calories=_sum_or_none([r.activity_calories for r in day_rows]),
            activity_days_count=sum(1 for r in day_rows if r.activity_count > 0),
            sleep_total_s=_sum_or_none([r.sleep_total_s for r in day_rows]),
            sleep_score=(
                sum(r.sleep_score for r in day_rows if r.sleep_score is not None)
                / len([r for r in day_rows if r.sleep_score is not None])
                if any(r.sleep_score is not None for r in day_rows)
                else None
            ),
            refreshed_at=now,
        )
    )

    health_rows = conn.execute(
        select(
            health_metric_daily_rollup.c.metric_key,
            health_metric_daily_rollup.c.local_date,
            health_metric_daily_rollup.c.value_sum,
            health_metric_daily_rollup.c.value_min,
            health_metric_daily_rollup.c.value_max,
            health_metric_daily_rollup.c.value_last,
            health_metric_daily_rollup.c.n_observations,
        ).where(
            health_metric_daily_rollup.c.athlete_id == athlete_id,
            health_metric_daily_rollup.c.local_date >= period_start,
            health_metric_daily_rollup.c.local_date <= period_end,
        )
    ).fetchall()

    by_metric: dict[str, list[PeriodHealthRow]] = defaultdict(list)
    for row in health_rows:
        by_metric[row.metric_key].append(
            (
                row.local_date,
                row.value_sum,
                row.value_min,
                row.value_max,
                row.value_last,
                row.n_observations,
            )
        )

    for metric_key, rows in by_metric.items():
        n_observations = sum(n for _, _, _, _, _, n in rows)
        value_sum = _sum_or_none([s for _, s, _, _, _, _ in rows])
        mins = [mn for _, _, mn, _, _, _ in rows if mn is not None]
        maxes = [mx for _, _, _, mx, _, _ in rows if mx is not None]
        # value_last: the value_last from the day with the greatest local_date among days that
        # actually have observations -- not just the last row in insertion order.
        dated_rows = [r for r in rows if r[5] > 0]
        value_last = max(dated_rows, key=lambda r: r[0])[4] if dated_rows else None
        conn.execute(
            health_metric_period_rollup.insert().values(
                athlete_id=athlete_id,
                period_type=period_type,
                period_start=period_start,
                metric_key=metric_key,
                value_sum=value_sum,
                value_avg=(
                    value_sum / n_observations
                    if value_sum is not None and n_observations > 0
                    else None
                ),
                value_min=min(mins) if mins else None,
                value_max=max(maxes) if maxes else None,
                value_last=value_last,
                n_observations=n_observations,
                refreshed_at=now,
            )
        )
