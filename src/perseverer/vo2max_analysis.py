"""Point-in-time diagnostic breakdown of the athlete's own independently-computed VO2max
(`performance_daily_rollup.rolling_vdot` -- see `performance_rollup.py`'s own docstring for the
Daniels-Gilbert model this is built on): which run set the current 42-day trailing maximum, what
else qualified in that same window, and what's missing (no qualifying run at all yet, the driving
run about to age out with nothing to replace it, or a long gap since the last qualifying effort).

Deliberately request-time, not rollup-backed, unlike `GET /performance` itself: the window this
scans is tiny (`ROLLING_VDOT_WINDOW_DAYS` of the athlete's own running activities -- typically a
handful of rows), a world apart from the multi-year aggregates the rollup mandate (AGENTS.md)
exists for. `GET /activities/needs-trim` and `GET /activities/possible-duplicates`
(`api/routers/activities.py`) already establish this same "bounded, occasional diagnostic lookup"
exception -- this isn't a new precedent, just the same one applied to VO2max.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Connection, select

from perseverer.db.schema import activity, activity_metric
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.performance_rollup import ROLLING_VDOT_WINDOW_DAYS

# Half the rolling window -- a judgment call flagging when the athlete hasn't put in a fresh
# quality effort for a while, even though the window technically still holds older qualifying
# data (so `rolling_vdot` itself isn't yet null).
_STALE_EFFORT_DAYS = ROLLING_VDOT_WINDOW_DAYS // 2
# Flag an upcoming drop only this close to it -- far enough out to be a useful heads-up, not so
# far that a value stable for weeks shows a "changing soon" warning the whole time.
_EXPIRING_SOON_DAYS = 7


@dataclass
class Vo2maxContributor:
    activity_id: str
    local_date: str
    name: str | None
    sport: str
    distance_m: float | None
    duration_s: float | None
    vdot: float


@dataclass
class Vo2maxFactorAnalysis:
    as_of: str
    window_start: str
    window_end: str
    rolling_vdot: float | None
    # The run whose own VDOT currently equals `rolling_vdot` -- a rolling *maximum*, not an
    # average, so this is the one activity actually setting the value, never several jointly.
    driving_activity: Vo2maxContributor | None
    # Every other qualifying run in the window, sorted by VDOT descending -- these did NOT set
    # the current value, but are what takes over if the driving run ages out before a stronger
    # one replaces it.
    other_contributors: list[Vo2maxContributor]
    # First date `driving_activity` no longer counts (its own local_date + the window length) --
    # `None` when there's no driving activity to expire.
    expires_on: str | None
    days_since_last_qualifying_run: int | None
    missing: list[str]


def compute_vo2max_factor_analysis(
    conn: Connection, *, athlete_id: str, as_of: date
) -> Vo2maxFactorAnalysis:
    """Mirrors `performance_rollup.py::refresh_performance_rollup`'s own `rolling_vdot` window
    exactly (same metric key, same `deleted_at is None` filter, same `[as_of - (window-1), as_of]`
    bounds) so `driving_activity.vdot` always equals that day's stored `rolling_vdot` -- this is a
    read of the same underlying facts through a different lens, not a second computation that
    could drift from the rollup's own value.
    """
    window_start = as_of - timedelta(days=ROLLING_VDOT_WINDOW_DAYS - 1)

    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.name,
            activity.c.sport,
            activity.c.distance_m,
            activity.c.moving_duration_s,
            activity_metric.c.value_num,
        )
        .select_from(
            activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id)
        )
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == VDOT_METRIC_KEY,
            activity.c.deleted_at.is_(None),
            activity.c.local_date >= window_start.isoformat(),
            activity.c.local_date <= as_of.isoformat(),
        )
        .order_by(activity_metric.c.value_num.desc())
    ).fetchall()

    contributors = [
        Vo2maxContributor(
            activity_id=r.id,
            local_date=r.local_date,
            name=r.name,
            sport=r.sport,
            distance_m=r.distance_m,
            duration_s=r.moving_duration_s,
            vdot=r.value_num,
        )
        for r in rows
    ]
    driving = contributors[0] if contributors else None
    others = contributors[1:]

    expires_on = None
    if driving is not None:
        expires_on = (
            date.fromisoformat(driving.local_date) + timedelta(days=ROLLING_VDOT_WINDOW_DAYS)
        ).isoformat()

    most_recent_local_date = conn.execute(
        select(activity.c.local_date)
        .select_from(
            activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id)
        )
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == VDOT_METRIC_KEY,
            activity.c.deleted_at.is_(None),
            activity.c.local_date <= as_of.isoformat(),
        )
        .order_by(activity.c.local_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    days_since_last = (
        (as_of - date.fromisoformat(most_recent_local_date)).days
        if most_recent_local_date is not None
        else None
    )

    missing: list[str] = []
    if driving is None:
        missing.append(
            "No qualifying run yet — VO2max needs at least one run lasting roughly 11+ minutes "
            "with distance and pace data; shorter efforts don't fit the aerobic model this is "
            "built on."
        )
    else:
        if expires_on is not None and (date.fromisoformat(expires_on) - as_of).days <= (
            _EXPIRING_SOON_DAYS
        ):
            if others:
                missing.append(
                    f"The run driving this value ages out of the {ROLLING_VDOT_WINDOW_DAYS}-day "
                    f"window on {expires_on} — the next-best effort already in the window takes "
                    "over unless a stronger one comes first."
                )
            else:
                missing.append(
                    f"The only qualifying run in the window ages out on {expires_on} with "
                    "nothing to replace it — without a new qualifying effort before then, "
                    "VO2max will show no value."
                )
        if days_since_last is not None and days_since_last >= _STALE_EFFORT_DAYS:
            missing.append(
                f"Your last qualifying run was {days_since_last} days ago — a fresh hard effort "
                "(tempo run, time trial, or race) would give a more current read."
            )
        if len(contributors) == 1:
            missing.append(
                "Based on a single qualifying run in the window — more efforts would make this "
                "estimate more robust against one unusually good or bad day."
            )

    return Vo2maxFactorAnalysis(
        as_of=as_of.isoformat(),
        window_start=window_start.isoformat(),
        window_end=as_of.isoformat(),
        rolling_vdot=driving.vdot if driving is not None else None,
        driving_activity=driving,
        other_contributors=others,
        expires_on=expires_on,
        days_since_last_qualifying_run=days_since_last,
        missing=missing,
    )
