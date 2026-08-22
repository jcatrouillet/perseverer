"""Orchestrates the insight engine: assembles the bounded, pure-Python inputs every rule module
needs (via real DB queries -- this is the one place in the package that touches the database),
calls each rule module, and writes the result to the `insight` table as a full
delete-and-reinsert per athlete per run (same justified precedent as fitness.py's full CTL/ATL/
TSB recompute -- cheap at this data volume, avoids stale rows lingering).

Called from two places, per ADR 0012's one genuinely non-obvious design point in this phase:
every ingest entry point's own touched-dates refresh call (like every other rollup here), *and*
once daily from the worker's own APScheduler job -- a window like "last 30 days" shifts every
day even with zero new ingests, unlike every other rollup in this codebase.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from sqlalchemy import Connection, case, select

from perseverer.db.schema import (
    activity,
    activity_metric,
    fitness_daily_rollup,
    health_metric_daily_rollup,
    sleep_session,
)
from perseverer.db.schema import (
    insight as insight_table,
)
from perseverer.insights.rules_efforts import compute_effort_insights
from perseverer.insights.rules_health import compute_health_insights
from perseverer.insights.rules_load import FitnessDay, compute_load_insights
from perseverer.insights.rules_pb import compute_pb_insights, compute_window_best_insights
from perseverer.insights.rules_streaks import compute_streak_insights
from perseverer.insights.types import Insight, InsightActivity
from perseverer.merge.engine import sport_family

# Same alias list/priority as api/routers/health.py::LOGICAL_METRICS["resting_heart_rate"] --
# duplicated rather than imported to keep the insight engine from depending on the API layer.
_RESTING_HR_ALIASES = (
    "resting_heart_rate",
    "garmin.daily_summary.restingHeartRate",
    "garmin.export.UDSFile.restingHeartRate",
)

# fit.* keys are only ever emitted by the FIT parser; strava.session.* is the source-honest
# alternative strava_export.py's CSV-totals overlay emits for GPX/TCX-sourced activities, which
# have no FIT session message to read avg/max HR or total descent from at all. Same alias-merge
# shape as api/routers/activities.py's AVG_HR_METRIC_KEYS/MAX_HR_METRIC_KEYS -- see ADR 0013.
_AVG_HR_METRIC_KEYS = ("fit.session.avg_heart_rate", "strava.session.avg_heart_rate")
_MAX_HR_METRIC_KEYS = ("fit.session.max_heart_rate", "strava.session.max_heart_rate")
_ELEVATION_LOSS_METRIC_KEYS = ("fit.session.total_descent", "strava.session.total_descent")
_CADENCE_METRIC_KEY = "fit.session.avg_running_cadence"
_MAX_CADENCE_METRIC_KEY = "fit.session.max_running_cadence"
_WEATHER_MIN_KEY = "weather.open_meteo.temperature_min_c"
_WEATHER_MAX_KEY = "weather.open_meteo.temperature_max_c"


def _scalar_metric_subquery(metric_key: str):  # type: ignore[no-untyped-def]
    return (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key == metric_key,
        )
        .limit(1)
        .scalar_subquery()
    )


def _aliased_metric_subquery(keys: tuple[str, ...]):  # type: ignore[no-untyped-def]
    """A correlated scalar subquery preferring the first key in `keys` that has a value for
    this activity -- `keys` is ordered by priority, not just membership. Same shape as
    api/routers/activities.py's helper of the same name (not shared -- two small, differently-
    scoped modules, not worth a new shared module for one function)."""
    priority = case(*[(activity_metric.c.metric_key == k, i) for i, k in enumerate(keys)])
    return (
        select(activity_metric.c.value_num)
        .where(
            activity_metric.c.activity_id == activity.c.id,
            activity_metric.c.metric_key.in_(keys),
        )
        .order_by(priority)
        .limit(1)
        .scalar_subquery()
    )


def load_insight_activities(conn: Connection, athlete_id: str) -> list[InsightActivity]:
    """Public (not `_`-prefixed) since api/routers/activities.py's per-activity insight endpoint
    reuses this same loader at request time -- the athlete-wide `refresh_insights` below is no
    longer this function's only caller."""
    avg_hr_subq = _aliased_metric_subquery(_AVG_HR_METRIC_KEYS)
    max_hr_subq = _aliased_metric_subquery(_MAX_HR_METRIC_KEYS)
    elevation_loss_subq = _aliased_metric_subquery(_ELEVATION_LOSS_METRIC_KEYS)
    cadence_raw_subq = _scalar_metric_subquery(_CADENCE_METRIC_KEY)
    max_cadence_raw_subq = _scalar_metric_subquery(_MAX_CADENCE_METRIC_KEY)
    temp_min_subq = _scalar_metric_subquery(_WEATHER_MIN_KEY)
    temp_max_subq = _scalar_metric_subquery(_WEATHER_MAX_KEY)

    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.start_time_utc,
            activity.c.utc_offset_s,
            activity.c.local_date,
            activity.c.sport,
            activity.c.name,
            activity.c.distance_m,
            activity.c.duration_s,
            activity.c.moving_duration_s,
            activity.c.elevation_gain_m,
            avg_hr_subq.label("avg_hr"),
            max_hr_subq.label("max_hr"),
            elevation_loss_subq.label("elevation_loss_m"),
            cadence_raw_subq.label("cadence_raw"),
            max_cadence_raw_subq.label("max_cadence_raw"),
            temp_min_subq.label("temperature_min_c"),
            temp_max_subq.label("temperature_max_c"),
        ).where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            activity.c.local_date.is_not(None),
        )
    ).fetchall()

    out: list[InsightActivity] = []
    for r in rows:
        family = sport_family(r.sport)
        # avg/max_running_cadence are single-foot rates (see api/routers/activities.py's own
        # doubling convention) -- only meaningful for foot sports, so left None otherwise
        # rather than reporting a cyclist's cadence under a running-shaped metric key.
        cadence = r.cadence_raw * 2 if r.cadence_raw is not None and family == "run" else None
        max_cadence = (
            r.max_cadence_raw * 2 if r.max_cadence_raw is not None and family == "run" else None
        )
        out.append(
            InsightActivity(
                id=r.id,
                start_time_utc=r.start_time_utc,
                utc_offset_s=r.utc_offset_s,
                local_date=r.local_date,
                sport=r.sport,
                sport_family=family,
                name=r.name,
                distance_m=r.distance_m,
                duration_s=r.duration_s,
                moving_duration_s=r.moving_duration_s,
                avg_hr=r.avg_hr,
                max_hr=r.max_hr,
                cadence=cadence,
                max_cadence=max_cadence,
                elevation_gain_m=r.elevation_gain_m,
                elevation_loss_m=r.elevation_loss_m,
                temperature_min_c=r.temperature_min_c,
                temperature_max_c=r.temperature_max_c,
            )
        )
    return out


def _load_fitness_days(conn: Connection, athlete_id: str) -> list[FitnessDay]:
    rows = conn.execute(
        select(
            fitness_daily_rollup.c.local_date,
            fitness_daily_rollup.c.training_load,
            fitness_daily_rollup.c.ctl,
            fitness_daily_rollup.c.atl,
            fitness_daily_rollup.c.tsb,
        )
        .where(fitness_daily_rollup.c.athlete_id == athlete_id)
        .order_by(fitness_daily_rollup.c.local_date.asc())
    ).fetchall()
    return [
        FitnessDay(
            local_date=r.local_date,
            training_load=r.training_load,
            ctl=r.ctl,
            atl=r.atl,
            tsb=r.tsb,
        )
        for r in rows
    ]


def _load_resting_hr_series(conn: Connection, athlete_id: str) -> list[tuple[str, float]]:
    rows = conn.execute(
        select(
            health_metric_daily_rollup.c.local_date,
            health_metric_daily_rollup.c.metric_key,
            health_metric_daily_rollup.c.value_last,
        )
        .where(
            health_metric_daily_rollup.c.athlete_id == athlete_id,
            health_metric_daily_rollup.c.metric_key.in_(_RESTING_HR_ALIASES),
            health_metric_daily_rollup.c.value_last.is_not(None),
        )
        .order_by(health_metric_daily_rollup.c.local_date.asc())
    ).fetchall()
    # First alias (in priority order) with data wins per day -- same rule as
    # api/routers/health.py::_merge_logical_metric.
    by_date: dict[str, float] = {}
    for alias in _RESTING_HR_ALIASES:
        for r in rows:
            if r.metric_key == alias and r.local_date not in by_date:
                by_date[r.local_date] = r.value_last
    return sorted(by_date.items())


def _load_sleep_score_series(conn: Connection, athlete_id: str) -> list[tuple[str, float]]:
    rows = conn.execute(
        select(sleep_session.c.local_date, sleep_session.c.sleep_score)
        .where(
            sleep_session.c.athlete_id == athlete_id,
            sleep_session.c.sleep_score.is_not(None),
        )
        .order_by(sleep_session.c.local_date.asc())
    ).fetchall()
    by_date: dict[str, list[float]] = {}
    for r in rows:
        by_date.setdefault(r.local_date, []).append(r.sleep_score)
    return sorted((d, sum(vs) / len(vs)) for d, vs in by_date.items())


def refresh_insights(conn: Connection, *, athlete_id: str, as_of: date | None = None) -> int:
    """Recomputes every insight for `athlete_id` and replaces the athlete's rows in `insight`
    wholesale. Never calls `conn.commit()` -- caller controls the transaction, matching every
    other refresh_* function in this codebase.
    """
    today = as_of or datetime.now(UTC).date()

    activities = load_insight_activities(conn, athlete_id)
    fitness_days = _load_fitness_days(conn, athlete_id)
    resting_hr = _load_resting_hr_series(conn, athlete_id)
    sleep_score = _load_sleep_score_series(conn, athlete_id)

    insights: list[Insight] = []
    insights.extend(compute_effort_insights(activities, today))
    insights.extend(compute_streak_insights(activities, today))
    insights.extend(compute_pb_insights(activities, today))
    insights.extend(compute_window_best_insights(activities, today))
    insights.extend(compute_load_insights(fitness_days, today))
    insights.extend(compute_health_insights(resting_hr, sleep_score, today))

    conn.execute(insight_table.delete().where(insight_table.c.athlete_id == athlete_id))
    now = datetime.now(UTC)
    for i in insights:
        conn.execute(
            insight_table.insert().values(
                athlete_id=athlete_id,
                kind=i.kind,
                window=i.window,
                subject_key=i.subject_key,
                metric_key=i.metric_key,
                sport_family=i.sport_family,
                activity_id=i.activity_id,
                local_date=i.local_date,
                title=i.title,
                detail=json.dumps(i.detail),
                value_num=i.value_num,
                computed_at=now,
            )
        )
    return len(insights)
