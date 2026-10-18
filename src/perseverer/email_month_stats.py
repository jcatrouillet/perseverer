"""The data behind the monthly email's mirror of the web app's Month view (MonthView.tsx): the
month-stats block, the Running and Cycling sections (RunningStats.tsx / CyclingStats.tsx), fitness
and form, and the wellness figures.

Pure reads, no rendering -- email_reports.py turns these into HTML and text. Same cross-language
port precedent as email_week_stats.py and sharing.py. Running matches `sport == "running"`
exactly (AGENTS.md); cycling is the "ride" sport family.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from perseverer.db.schema import (
    activity,
    activity_metric,
    fitness_daily_rollup,
    health_metric_daily_rollup,
    sleep_session,
)
from perseverer.email_week_stats import (
    IMPLAUSIBLE_RUN_PACE_MIN_PER_KM,
    RIDE_PR_DISTANCES,
    RUN_PR_DISTANCES,
)
from perseverer.merge.engine import sports_in_family

AVG_HR_KEYS = ("fit.session.avg_heart_rate", "strava.session.avg_heart_rate")
MAX_HR_KEYS = ("fit.session.max_heart_rate", "strava.session.max_heart_rate")
VDOT_KEY = "perseverer.performance.vdot"

# Same alias lists, in the same priority order, as api/routers/health.py::LOGICAL_METRICS --
# duplicated rather than imported to keep this module off the API layer (email_reports.py's own
# _STEPS_ALIASES precedent). (aliases, which rollup column carries the day's value)
STEPS_ALIASES = ("garmin.daily_summary.totalSteps", "garmin.export.UDSFile.totalSteps")
RESTING_HR_ALIASES = (
    "resting_heart_rate",
    "garmin.daily_summary.restingHeartRate",
    "garmin.export.UDSFile.restingHeartRate",
)
HRV_ALIASES = (
    "hrv.last_night_average",
    "garmin.export.TrainingReadinessDTO.hrvWeeklyAverage",
    "garmin.daily_hrv.lastNightAvg",
)
WEIGHT_ALIASES = ("eufy.scale.weight", "apple_health.body_mass")

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class DatedValue:
    value: float
    date: str


@dataclass(frozen=True)
class RecordLine:
    label: str
    date: str
    distance_m: float
    duration_s: float
    is_new_pr: bool  # the all-time best, not just the month's


@dataclass(frozen=True)
class WeekBar:
    week_start: str  # the Monday; only the days inside the month count toward `km`
    km: float


@dataclass(frozen=True)
class SportMonth:
    sport: str  # "running" | "cycling"
    count: int
    distance_m: float
    prior_month_distance_m: float
    prior_year_distance_m: float
    duration_s: float
    longest: DatedValue | None  # metres
    best_vdot: DatedValue | None  # running
    max_hr: DatedValue | None
    avg_length_m: float
    days_per_week: float
    most_often: str
    least_often: str
    longest_streak_days: int
    longest_break_days: int
    records: list[RecordLine] = field(default_factory=list)
    weekly: list[WeekBar] = field(default_factory=list)

    @property
    def avg_pace_s_per_km(self) -> float | None:
        return self.duration_s / (self.distance_m / 1000) if self.distance_m > 0 else None

    @property
    def avg_speed_kmh(self) -> float | None:
        return (
            self.distance_m / 1000 / (self.duration_s / 3600)
            if self.distance_m > 0 and self.duration_s > 0
            else None
        )


@dataclass(frozen=True)
class OverallMonth:
    """PeriodStatsCard's figures that the rollup row doesn't carry."""

    longest_streak_days: int
    longest_activity: DatedValue | None  # metres
    busiest_week_start: str | None
    favorite_day: str | None
    avg_distance_m: float | None
    avg_duration_s: float | None
    avg_speed_kmh: float | None
    avg_hr_bpm: float | None
    max_hr_bpm: float | None


@dataclass(frozen=True)
class FitnessMonth:
    load: float
    ctl: float
    atl: float
    tsb: float
    ctl_change: float | None  # vs the previous month's last day


@dataclass(frozen=True)
class WellnessMonth:
    avg_sleep_s: float | None
    avg_steps: float | None
    avg_resting_hr: float | None
    avg_hrv: float | None
    weight_first: DatedValue | None
    weight_last: DatedValue | None


@dataclass(frozen=True)
class MonthStats:
    overall: OverallMonth | None
    fitness: FitnessMonth | None
    wellness: WellnessMonth | None
    running: SportMonth | None
    cycling: SportMonth | None


@dataclass(frozen=True)
class _Act:
    id: str
    sport: str
    local_date: str
    distance_m: float | None
    duration_s: float | None
    avg_hr: float | None = None
    max_hr: float | None = None
    vdot: float | None = None


def _first(metrics: dict[str, float], keys: Sequence[str]) -> float | None:
    for k in keys:
        if k in metrics:
            return metrics[k]
    return None


def _load_activities(
    conn: Connection, athlete_id: str, sports: Sequence[str] | None, end: str
) -> list[_Act]:
    query = select(
        activity.c.id,
        activity.c.sport,
        activity.c.local_date,
        activity.c.distance_m,
        activity.c.moving_duration_s,
        activity.c.duration_s,
    ).where(
        and_(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            activity.c.local_date.is_not(None),
            activity.c.local_date <= end,
        )
    )
    if sports is not None:
        query = query.where(activity.c.sport.in_(sports))
    rows = conn.execute(query).fetchall()
    return [
        _Act(
            id=r.id,
            sport=r.sport,
            local_date=r.local_date,
            distance_m=r.distance_m,
            duration_s=r.moving_duration_s if r.moving_duration_s is not None else r.duration_s,
        )
        for r in rows
    ]


def _with_metrics(conn: Connection, acts: list[_Act]) -> list[_Act]:
    if not acts:
        return acts
    by_id: dict[str, dict[str, float]] = {}
    keys = (*AVG_HR_KEYS, *MAX_HR_KEYS, VDOT_KEY)
    for chunk_start in range(0, len(acts), 500):
        ids = [a.id for a in acts[chunk_start : chunk_start + 500]]
        for r in conn.execute(
            select(
                activity_metric.c.activity_id,
                activity_metric.c.metric_key,
                activity_metric.c.value_num,
            ).where(
                and_(activity_metric.c.activity_id.in_(ids), activity_metric.c.metric_key.in_(keys))
            )
        ):
            if r.value_num is not None:
                by_id.setdefault(r.activity_id, {})[r.metric_key] = r.value_num
    return [
        _Act(
            a.id,
            a.sport,
            a.local_date,
            a.distance_m,
            a.duration_s,
            _first(by_id.get(a.id, {}), AVG_HR_KEYS),
            _first(by_id.get(a.id, {}), MAX_HR_KEYS),
            by_id.get(a.id, {}).get(VDOT_KEY),
        )
        for a in acts
    ]


def _in(acts: list[_Act], start: str, end: str) -> list[_Act]:
    return [a for a in acts if start <= a.local_date <= end]


def _total_m(acts: list[_Act]) -> float:
    return sum(a.distance_m or 0.0 for a in acts)


def _streak_and_break(dates: list[str]) -> tuple[int, int]:
    """runningStats.ts::longestStreakAndBreak -- the longest run of consecutive active days and
    the longest stretch of inactive days strictly between two active ones."""
    if not dates:
        return 0, 0
    longest = current = 1
    longest_break = 0
    for prev, cur in pairwise(dates):
        gap = (date.fromisoformat(cur) - date.fromisoformat(prev)).days
        current = current + 1 if gap == 1 else 1
        longest = max(longest, current)
        longest_break = max(longest_break, gap - 1)
    return longest, longest_break


def _weekday_counts(acts: list[_Act]) -> list[int]:
    counts = [0] * 7
    for a in acts:
        counts[date.fromisoformat(a.local_date).weekday()] += 1
    return counts


def _favorite_day(acts: list[_Act]) -> str | None:
    counts = _weekday_counts(acts)
    return WEEKDAYS[max(range(7), key=lambda i: (counts[i], -i))] if any(counts) else None


def _busiest_week(acts: list[_Act]) -> str | None:
    counts: dict[str, int] = {}
    for a in acts:
        d = date.fromisoformat(a.local_date)
        monday = (d - timedelta(days=d.weekday())).isoformat()
        counts[monday] = counts.get(monday, 0) + 1
    return max(sorted(counts), key=lambda k: counts[k]) if counts else None


def _weekly_bars(acts: list[_Act], start: date, end: date) -> list[WeekBar]:
    """Monday-based weeks overlapping the month, counting only the days inside it."""
    monday = start - timedelta(days=start.weekday())
    bars: list[WeekBar] = []
    while monday <= end:
        lo, hi = max(monday, start), min(monday + timedelta(days=6), end)
        bars.append(
            WeekBar(monday.isoformat(), _total_m(_in(acts, lo.isoformat(), hi.isoformat())) / 1000)
        )
        monday += timedelta(days=7)
    return bars


def _best_by_distance(
    acts: list[_Act], distances: tuple[tuple[str, float], ...]
) -> dict[str, _Act]:
    """For each standard distance, the fastest whole activity within 0.9x-1.3x of it."""
    best: dict[str, _Act] = {}
    for label, meters in distances:
        eligible = [
            a
            for a in acts
            if a.distance_m is not None
            and meters * 0.9 <= a.distance_m <= meters * 1.3
            and a.duration_s
        ]
        if eligible:
            best[label] = min(eligible, key=lambda a: a.duration_s / a.distance_m)  # type: ignore[operator]
    return best


def _sport_month(
    all_acts: list[_Act], *, sport: str, start: date, end: date, prior_start: date, prior_end: date,
    year_start: date, year_end: date,
) -> SportMonth | None:  # fmt: skip
    running = sport == "running"
    acts = [a for a in all_acts if a.local_date <= end.isoformat()]
    if running:
        acts = [
            a
            for a in acts
            if not (a.distance_m and a.duration_s)
            or a.duration_s / 60 / (a.distance_m / 1000) <= IMPLAUSIBLE_RUN_PACE_MIN_PER_KM
        ]
    month = _in(acts, start.isoformat(), end.isoformat())
    if not month:
        return None
    dates = sorted({a.local_date for a in month})
    streak, brk = _streak_and_break(dates)
    span_days = max(1, (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days + 1)
    counts = _weekday_counts(month)
    with_distance = [a for a in month if a.distance_m]
    longest_act = max(with_distance, key=lambda a: a.distance_m or 0.0, default=None)
    hr_acts = [a for a in month if a.max_hr is not None]
    best_hr = max(hr_acts, key=lambda a: a.max_hr or 0.0, default=None)
    vdot_acts = [a for a in month if a.vdot]
    best_vdot = max(vdot_acts, key=lambda a: a.vdot or 0.0, default=None)

    distances = RUN_PR_DISTANCES if running else RIDE_PR_DISTANCES
    all_best = _best_by_distance(acts, distances)
    month_best = _best_by_distance(month, distances)
    records = [
        RecordLine(
            label=label,
            date=a.local_date,
            distance_m=a.distance_m or 0.0,
            duration_s=a.duration_s or 0.0,
            is_new_pr=all_best[label].id == a.id,
        )
        for label, a in month_best.items()
    ]
    return SportMonth(
        sport=sport,
        count=len(month),
        distance_m=_total_m(month),
        prior_month_distance_m=_total_m(_in(acts, prior_start.isoformat(), prior_end.isoformat())),
        prior_year_distance_m=_total_m(_in(acts, year_start.isoformat(), year_end.isoformat())),
        duration_s=sum(a.duration_s or 0.0 for a in with_distance),
        longest=DatedValue(longest_act.distance_m or 0.0, longest_act.local_date)
        if longest_act
        else None,
        best_vdot=DatedValue(best_vdot.vdot or 0.0, best_vdot.local_date) if best_vdot else None,
        max_hr=DatedValue(best_hr.max_hr or 0.0, best_hr.local_date) if best_hr else None,
        avg_length_m=_total_m(month) / len(month),
        days_per_week=len(dates) / (span_days / 7) if span_days >= 7 else float(len(dates)),
        most_often=WEEKDAYS[max(range(7), key=lambda i: (counts[i], -i))],
        least_often=WEEKDAYS[min(range(7), key=lambda i: (counts[i], i))],
        longest_streak_days=streak,
        longest_break_days=brk,
        records=records,
        weekly=_weekly_bars(acts, start, end),
    )


def _overall(month: list[_Act]) -> OverallMonth | None:
    if not month:
        return None
    dates = sorted({a.local_date for a in month})
    with_distance = [a for a in month if a.distance_m]
    longest = max(with_distance, key=lambda a: a.distance_m or 0.0, default=None)
    timed = [a for a in month if a.distance_m is not None and a.duration_s]
    total_s = sum(a.duration_s or 0.0 for a in timed)
    durations = [a.duration_s for a in month if a.duration_s is not None]
    hrs = [a.avg_hr for a in month if a.avg_hr is not None]
    max_hrs = [a.max_hr for a in month if a.max_hr is not None]
    return OverallMonth(
        longest_streak_days=_streak_and_break(dates)[0],
        longest_activity=DatedValue(longest.distance_m or 0.0, longest.local_date)
        if longest
        else None,
        busiest_week_start=_busiest_week(month),
        favorite_day=_favorite_day(month),
        avg_distance_m=(
            sum(a.distance_m or 0.0 for a in with_distance) / len(with_distance)
            if with_distance
            else None
        ),
        avg_duration_s=sum(durations) / len(durations) if durations else None,
        avg_speed_kmh=(
            sum(a.distance_m or 0.0 for a in timed) / 1000 / (total_s / 3600) if total_s else None
        ),
        avg_hr_bpm=sum(hrs) / len(hrs) if hrs else None,
        max_hr_bpm=max(max_hrs) if max_hrs else None,
    )


def _fitness_month(
    conn: Connection, athlete_id: str, start: date, end: date
) -> FitnessMonth | None:
    prior_end = start - timedelta(days=1)
    rows = conn.execute(
        select(fitness_daily_rollup).where(
            and_(
                fitness_daily_rollup.c.athlete_id == athlete_id,
                fitness_daily_rollup.c.local_date >= prior_end.isoformat(),
                fitness_daily_rollup.c.local_date <= end.isoformat(),
            )
        )
    ).fetchall()
    in_month = [r for r in rows if start.isoformat() <= r.local_date <= end.isoformat()]
    last = max(in_month, key=lambda r: r.local_date, default=None)
    if last is None:
        return None
    prior = next((r for r in rows if r.local_date == prior_end.isoformat()), None)
    return FitnessMonth(
        load=sum(r.training_load for r in in_month),
        ctl=last.ctl,
        atl=last.atl,
        tsb=last.tsb,
        ctl_change=last.ctl - prior.ctl if prior is not None else None,
    )


def _daily_values(
    conn: Connection, athlete_id: str, aliases: tuple[str, ...], column: str, start: str, end: str
) -> dict[str, float]:
    """One value per date, from the first alias (in priority order) that has one that day."""
    rows = conn.execute(
        select(
            health_metric_daily_rollup.c.local_date,
            health_metric_daily_rollup.c.metric_key,
            getattr(health_metric_daily_rollup.c, column).label("v"),
        ).where(
            and_(
                health_metric_daily_rollup.c.athlete_id == athlete_id,
                health_metric_daily_rollup.c.metric_key.in_(aliases),
                health_metric_daily_rollup.c.local_date >= start,
                health_metric_daily_rollup.c.local_date <= end,
            )
        )
    ).fetchall()
    best: dict[str, int] = {}
    values: dict[str, float] = {}
    for r in rows:
        if r.v is None:
            continue
        priority = aliases.index(r.metric_key)
        if r.local_date not in best or priority < best[r.local_date]:
            best[r.local_date] = priority
            values[r.local_date] = r.v
    return values


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _wellness(conn: Connection, athlete_id: str, start: str, end: str) -> WellnessMonth | None:
    sleep_rows = conn.execute(
        select(sleep_session.c.local_date, sleep_session.c.total_sleep_s).where(
            and_(
                sleep_session.c.athlete_id == athlete_id,
                sleep_session.c.local_date >= start,
                sleep_session.c.local_date <= end,
            )
        )
    ).fetchall()
    per_night: dict[str, float] = {}
    for r in sleep_rows:
        if r.total_sleep_s:
            per_night[r.local_date] = max(per_night.get(r.local_date, 0.0), r.total_sleep_s)
    steps = _daily_values(conn, athlete_id, STEPS_ALIASES, "value_sum", start, end)
    resting = _daily_values(conn, athlete_id, RESTING_HR_ALIASES, "value_last", start, end)
    hrv = _daily_values(conn, athlete_id, HRV_ALIASES, "value_last", start, end)
    weight = _daily_values(conn, athlete_id, WEIGHT_ALIASES, "value_last", start, end)
    if not (per_night or steps or resting or hrv or weight):
        return None
    weight_dates = sorted(weight)
    return WellnessMonth(
        avg_sleep_s=_mean(list(per_night.values())),
        avg_steps=_mean([v for v in steps.values() if v]),
        avg_resting_hr=_mean(list(resting.values())),
        avg_hrv=_mean(list(hrv.values())),
        weight_first=DatedValue(weight[weight_dates[0]], weight_dates[0]) if weight_dates else None,
        weight_last=DatedValue(weight[weight_dates[-1]], weight_dates[-1])
        if weight_dates
        else None,
    )


def build_month_stats(conn: Connection, *, athlete_id: str, month_start: date) -> MonthStats:
    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
    prior_end = month_start - timedelta(days=1)
    prior_start = prior_end.replace(day=1)
    year_start = month_start.replace(year=month_start.year - 1)
    year_end = year_start.replace(day=calendar.monthrange(year_start.year, year_start.month)[1])
    end = month_end.isoformat()

    everything = _in(_load_activities(conn, athlete_id, None, end), month_start.isoformat(), end)
    overall = _overall(_with_metrics(conn, everything))

    def section(sport: str, sports: tuple[str, ...]) -> SportMonth | None:
        loaded = _load_activities(conn, athlete_id, sports, end)
        # Heart rate and VDOT only matter for the month's own activities.
        enriched = {a.id: a for a in _with_metrics(conn, _in(loaded, month_start.isoformat(), end))}
        acts = [enriched.get(a.id, a) for a in loaded]
        return _sport_month(
            acts,
            sport=sport,
            start=month_start,
            end=month_end,
            prior_start=prior_start,
            prior_end=prior_end,
            year_start=year_start,
            year_end=year_end,
        )

    return MonthStats(
        overall=overall,
        fitness=_fitness_month(conn, athlete_id, month_start, month_end),
        wellness=_wellness(conn, athlete_id, month_start.isoformat(), end),
        running=section("running", ("running",)),
        cycling=section("cycling", sports_in_family("ride")),
    )
