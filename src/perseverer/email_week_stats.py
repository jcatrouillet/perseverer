"""The data behind the weekly email's mirror of the web app's Week view (WeekView.tsx): the
week-stats block (with the previous week beside each figure, load and fitness/fatigue/form),
planned-workout compliance, and the Running and Cycling sections (totals, fastest pace/speed,
all-time PRs set that week, and the weekly-distance trend).

Pure reads, no rendering -- email_reports.py turns these into HTML and text. Ports
WeekRunningStats.tsx / WeekCyclingStats.tsx / WeekView.tsx's own arithmetic, same cross-language
duplication precedent as sharing.py, so the email and the page tell the same story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from perseverer.db.schema import (
    activity,
    activity_metric,
    fitness_daily_rollup,
    planned_workout,
)
from perseverer.merge.engine import sports_in_family
from perseverer.planned_workouts import activities_by_local_date, matching_activity_id

# runningStats.ts::IMPLAUSIBLE_RUN_PACE_MIN_PER_KM: a "run" slower than this is a walk or a
# mis-tagged activity, and is left out of every running figure.
IMPLAUSIBLE_RUN_PACE_MIN_PER_KM = 8.5
# runningStats.ts::STANDARD_DISTANCES / CYCLING_PR_DISTANCES: the whole recorded activity within
# 0.9x-1.3x of the distance counts as an attempt at it.
RUN_PR_DISTANCES: tuple[tuple[str, float], ...] = (
    ("1 mile", 1609.34),
    ("3 km", 3000.0),
    ("5 km", 5000.0),
    ("4 mile", 6437.38),
    ("5 mile", 8046.72),
    ("10 km", 10000.0),
    ("15 km", 15000.0),
    ("10 mile", 16093.4),
    ("20 km", 20000.0),
    ("Half marathon", 21097.5),
    ("Marathon", 42195.0),
)
RIDE_PR_DISTANCES: tuple[tuple[str, float], ...] = (
    ("5 km", 5000.0),
    ("10 km", 10000.0),
    ("20 km", 20000.0),
    ("40 km", 40000.0),
    ("100 km", 100000.0),
)
TREND_WEEKS = 12
WEIGHT_METRIC_KEY = "fit.user_profile.weight"


@dataclass(frozen=True)
class FitnessWeek:
    """The week's summed training load and its last day's CTL/ATL/TSB (cumulative state, not an
    average), plus the ramp: that CTL minus the previous week's last day's."""

    load: float
    ctl: float | None
    atl: float | None
    tsb: float | None
    ramp: float | None


@dataclass(frozen=True)
class ComplianceLine:
    sport: str
    scheduled: int
    completed: int

    @property
    def pct(self) -> int:
        return round(self.completed / self.scheduled * 100)


@dataclass(frozen=True)
class WeekPoint:
    week_start: str
    value: float  # kilometres


@dataclass(frozen=True)
class SportWeek:
    """One sport's section: this week's totals, the previous week's distance/duration, and the
    weekly-distance trend ending with this week."""

    sport: str  # "running" | "cycling"
    distance_m: float
    duration_s: float
    prior_distance_m: float
    prior_duration_s: float
    fastest_pace_s_per_km: float | None  # running
    fastest_speed_kmh: float | None  # cycling
    calories: float | None
    elevation_m: float | None
    mets: float | None  # running only (calories / body weight)
    new_prs: list[str] = field(default_factory=list)
    weekly_km: list[WeekPoint] = field(default_factory=list)

    @property
    def day_equivalent_pct(self) -> float:
        return self.duration_s / 86400 * 100


@dataclass(frozen=True)
class WeekStats:
    fitness: FitnessWeek | None
    compliance: list[ComplianceLine]
    running: SportWeek | None
    cycling: SportWeek | None


@dataclass(frozen=True)
class _Act:
    id: str
    local_date: str
    distance_m: float | None
    duration_s: float | None
    calories: float | None
    elevation_m: float | None


def _fitness_week(
    conn: Connection, athlete_id: str, week_start: date, week_end: date
) -> FitnessWeek | None:
    prior_end = week_start - timedelta(days=1)
    rows = conn.execute(
        select(fitness_daily_rollup)
        .where(
            and_(
                fitness_daily_rollup.c.athlete_id == athlete_id,
                fitness_daily_rollup.c.local_date >= (week_start - timedelta(days=7)).isoformat(),
                fitness_daily_rollup.c.local_date <= week_end.isoformat(),
            )
        )
        .order_by(fitness_daily_rollup.c.local_date)
    ).fetchall()
    in_week = [r for r in rows if week_start.isoformat() <= r.local_date <= week_end.isoformat()]
    if not in_week:
        return None
    end_row = next((r for r in in_week if r.local_date == week_end.isoformat()), None)
    prior_row = next((r for r in rows if r.local_date == prior_end.isoformat()), None)
    ramp = end_row.ctl - prior_row.ctl if end_row is not None and prior_row is not None else None
    return FitnessWeek(
        load=sum(r.training_load for r in in_week),
        ctl=end_row.ctl if end_row else None,
        atl=end_row.atl if end_row else None,
        tsb=end_row.tsb if end_row else None,
        ramp=ramp,
    )


def _compliance(
    conn: Connection, athlete_id: str, week_start: date, week_end: date, today: date
) -> list[ComplianceLine]:
    """WeekView.tsx::computeCompliance: count-based, done = completed or matched by a recorded
    activity that day, and only workouts dated up to `today` count."""
    rows = conn.execute(
        select(
            planned_workout.c.local_date, planned_workout.c.sport, planned_workout.c.completed_at
        ).where(
            and_(
                planned_workout.c.athlete_id == athlete_id,
                planned_workout.c.local_date >= week_start.isoformat(),
                planned_workout.c.local_date <= week_end.isoformat(),
            )
        )
    ).fetchall()
    if not rows:
        return []
    recorded = activities_by_local_date(
        conn, athlete_id, week_start.isoformat(), week_end.isoformat()
    )
    counts: dict[str, list[int]] = {}
    for r in rows:
        if r.local_date > today.isoformat():
            continue
        entry = counts.setdefault(r.sport, [0, 0])  # [scheduled, completed]
        entry[0] += 1
        if r.completed_at is not None or matching_activity_id(
            recorded.get(r.local_date, []), r.sport
        ):
            entry[1] += 1
    lines = [ComplianceLine(sport, s, c) for sport, (s, c) in counts.items()]
    return sorted(lines, key=lambda c: (-c.scheduled, c.sport))


def _activities(conn: Connection, athlete_id: str, sports: tuple[str, ...], end: str) -> list[_Act]:
    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.distance_m,
            activity.c.moving_duration_s,
            activity.c.duration_s,
            activity.c.calories,
            activity.c.elevation_gain_m,
        ).where(
            and_(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport.in_(sports),
                activity.c.local_date.is_not(None),
                activity.c.local_date <= end,
            )
        )
    ).fetchall()
    return [
        _Act(
            id=r.id,
            local_date=r.local_date,
            distance_m=r.distance_m,
            duration_s=r.moving_duration_s if r.moving_duration_s is not None else r.duration_s,
            calories=r.calories,
            elevation_m=r.elevation_gain_m,
        )
        for r in rows
    ]


def _plausible_run(a: _Act) -> bool:
    if not a.distance_m or a.distance_m <= 0 or a.duration_s is None:
        return True
    return a.duration_s / 60 / (a.distance_m / 1000) <= IMPLAUSIBLE_RUN_PACE_MIN_PER_KM


def _best_by_distance(
    acts: list[_Act], distances: tuple[tuple[str, float], ...]
) -> dict[str, tuple[str, str]]:
    """label -> (date, activity id) of the fastest whole activity in the 0.9x-1.3x band."""
    best: dict[str, tuple[str, str]] = {}
    for label, meters in distances:
        eligible = [
            a
            for a in acts
            if a.distance_m is not None
            and meters * 0.9 <= a.distance_m <= meters * 1.3
            and a.duration_s
        ]
        if not eligible:
            continue
        winner = min(eligible, key=lambda a: a.duration_s / a.distance_m)  # type: ignore[operator]
        best[label] = (winner.local_date, winner.id)
    return best


def _new_prs(
    this_week: list[_Act], all_time: list[_Act], distances: tuple[tuple[str, float], ...]
) -> list[str]:
    """Distances where this week holds the all-time best (not just the week's best)."""
    week_ids = {a.id for a in this_week}
    all_time_best = _best_by_distance(all_time, distances)
    return [label for label, (_, aid) in all_time_best.items() if aid in week_ids]


def _weekly_km(acts: list[_Act], week_start: date) -> list[WeekPoint]:
    first = week_start - timedelta(weeks=TREND_WEEKS - 1)
    totals = {first + timedelta(weeks=i): 0.0 for i in range(TREND_WEEKS)}
    for a in acts:
        d = date.fromisoformat(a.local_date)
        monday = d - timedelta(days=d.weekday())
        if monday in totals:
            totals[monday] += (a.distance_m or 0.0) / 1000
    return [WeekPoint(w.isoformat(), km) for w, km in sorted(totals.items())]


def _weights(conn: Connection, ids: list[str]) -> dict[str, float]:
    if not ids:
        return {}
    rows = conn.execute(
        select(activity_metric.c.activity_id, activity_metric.c.value_num).where(
            and_(
                activity_metric.c.activity_id.in_(ids),
                activity_metric.c.metric_key == WEIGHT_METRIC_KEY,
            )
        )
    ).fetchall()
    return {r.activity_id: r.value_num for r in rows if r.value_num}


def _sport_week(
    conn: Connection,
    athlete_id: str,
    *,
    sport: str,
    week_start: date,
    week_end: date,
) -> SportWeek | None:
    running = sport == "running"
    sports = ("running",) if running else sports_in_family("ride")
    acts = _activities(conn, athlete_id, sports, week_end.isoformat())
    if running:
        acts = [a for a in acts if _plausible_run(a)]
    start, end = week_start.isoformat(), week_end.isoformat()
    prior_start = (week_start - timedelta(days=7)).isoformat()
    prior_end = (week_start - timedelta(days=1)).isoformat()
    this_week = [a for a in acts if start <= a.local_date <= end]
    if not this_week:
        return None
    prior = [a for a in acts if prior_start <= a.local_date <= prior_end]

    def total(items: list[_Act], attr: str) -> float:
        return sum(getattr(a, attr) or 0.0 for a in items)

    timed = [a for a in this_week if a.distance_m and a.distance_m > 0 and a.duration_s]
    fastest_pace = min((a.duration_s / (a.distance_m / 1000) for a in timed), default=None)  # type: ignore[operator]
    fastest_speed = max((a.distance_m / 1000 / (a.duration_s / 3600) for a in timed), default=None)  # type: ignore[operator]
    calories = total(this_week, "calories")
    elevation = total(this_week, "elevation_m")

    mets: float | None = None
    if running:
        weights = _weights(conn, [a.id for a in this_week])
        met_minutes = sum(
            a.calories / weights[a.id] * 60 for a in this_week if a.calories and a.id in weights
        )
        mets = met_minutes or None

    distances = RUN_PR_DISTANCES if running else RIDE_PR_DISTANCES
    return SportWeek(
        sport=sport,
        distance_m=total(this_week, "distance_m"),
        duration_s=total(this_week, "duration_s"),
        prior_distance_m=total(prior, "distance_m"),
        prior_duration_s=total(prior, "duration_s"),
        fastest_pace_s_per_km=fastest_pace if running else None,
        fastest_speed_kmh=None if running else fastest_speed,
        calories=calories or None,
        elevation_m=elevation or None,
        mets=mets,
        new_prs=_new_prs(this_week, acts, distances),
        weekly_km=_weekly_km(acts, week_start),
    )


def build_week_stats(
    conn: Connection, *, athlete_id: str, week_start: date, today: date
) -> WeekStats:
    week_end = week_start + timedelta(days=6)
    return WeekStats(
        fitness=_fitness_week(conn, athlete_id, week_start, week_end),
        compliance=_compliance(conn, athlete_id, week_start, week_end, today),
        running=_sport_week(
            conn, athlete_id, sport="running", week_start=week_start, week_end=week_end
        ),
        cycling=_sport_week(
            conn, athlete_id, sport="cycling", week_start=week_start, week_end=week_end
        ),
    )
