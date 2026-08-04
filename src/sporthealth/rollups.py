"""Precomputed daily rollups — the platform's only sanctioned way for calendar/dashboard views
to read aggregate data. CLAUDE.md, non-negotiable: "the Celeron cannot aggregate a decade of
activities per request — every dashboard/calendar/recap view reads a `*_rollup` table refreshed
on ingest, never scans at request time."

`refresh_daily_rollup` is called once per distinct `local_date` an ingest run actually touched
(never once per file/record — see each adapter's ingest loop), by every ingest entry point:
`fit_folder.import_from_folder`, `garmin_export.import_garmin_export`,
`garmin_connect.sync_garmin_connect`, `rebuild.rebuild_database`. See
docs/adr/0006-phase-3-read-api-and-rollups.md decisions 1 and 3.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import Connection, delete, func, select

from sporthealth.db.schema import (
    activity,
    day_rollup,
    health_metric_daily_rollup,
    health_observation,
    sleep_session,
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
