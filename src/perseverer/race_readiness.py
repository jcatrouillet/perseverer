"""Race readiness: has the athlete actually run enough *volume* for their next scheduled race
(`planned_race`), not just "are they fit" -- a materially different question from the existing
VDOT-based race prediction (`vdot.py::predict_race_time_s`, already surfaced on `planned_race`
itself via `planned_races.py::predicted_duration_s_for_distance`). That prediction answers "what
could this athlete run today at their current fitness" from a single rolling VDOT number; it says
nothing about whether they've put in the specific weekly mileage and long runs a race of this
distance actually calls for. This module answers that second question and leaves the VDOT
prediction untouched, shown alongside as this module's own "prognosis" rather than re-derived or
blended into a new formula -- combining two numbers with different physiological bases (a
volume-adequacy fraction and a fitness-derived time) into one fabricated figure would overclaim
precision this app has no grounds for.

**Targets, by race distance** (`_WEEKLY_TARGET_ANCHORS`/`_LONG_RUN_TARGET_ANCHORS`): unlike VDOT,
"how much should you run for a marathon" has no single physiological equation -- it's coaching
judgment, and real published plans disagree by a factor of 2 depending on athlete level (e.g.
Hal Higdon Intermediate marathon peaks around 55km/34mi with a 29km/18mi long run, while Pfitzinger
Advanced plans peak past 110km/70mi with a 32km/20mi+ long run). This module deliberately targets
the *recreational/intermediate* end of that range (Higdon Novice/Intermediate, Daniels' Running
Formula's own easier plans) rather than an advanced/competitive baseline -- picking the advanced
number would read as "not ready" for the common recreational case this app is built for. Four
anchor points (5k/10k/half/marathon distance -> target), log-linear interpolated for anything in
between and clamped (never extrapolated) outside that range, same "honest rather than
extrapolated" posture `predict_race_time_s`'s own search bounds already establish.

**Compliance is recency-weighted, not a flat average** -- an exponential decay by days-ago,
mirroring the same EWMA philosophy `fitness_daily_rollup`'s own Coggan/Banister CTL(42d)/ATL(7d)
already uses in this codebase (recent training predicts race-day readiness far better than
training from months ago). Weekly running distance looks back `WEEKLY_DISTANCE_WINDOW_DAYS` (182
days / 26 weeks) with a `WEEKLY_DISTANCE_HALF_LIFE_DAYS` (28-day) half-life; the long run looks
back a shorter `LONG_RUN_WINDOW_DAYS` (70 days / 10 weeks) with a shorter `LONG_RUN_HALF_LIFE_DAYS`
(14-day) half-life, since a taper's own most recent long run matters far more than one from two
months out. Each week's own value is credited up to (never past) 100% of target -- a week that
doubled the target isn't "200% ready," it's just fully credited, same capping instinct
`_bar`/`_bar_rows` (email_reports.py) already apply elsewhere in this codebase to a comparison
against a target.

Both the actual realized numbers and the target are exposed, not just the compliance percentage
computed from them -- `weekly_distance_series`/`long_run_series` (`WeekValue`, one entry per
Monday-start week, `0.0` never omitted for a week with nothing recorded) are the dense series a
dedicated chart plots real bars for against a target reference line, distinct from `history`'s
own already-weighted/combined percentages.

**Combining the two into one readiness percentage** (`READINESS_WEEKLY_DISTANCE_WEIGHT` = 0.6,
`READINESS_LONG_RUN_WEIGHT` = 0.4): overall weekly volume is the primary driver of endurance-race
readiness in the same literature the targets above come from (Daniels, Pfitzinger both treat
total volume as the dominant training variable, with the long run as an important but secondary
specificity factor) -- hence weighted toward weekly distance. Like the target anchors above, this
is this app's own stated policy, not a claimed universal formula; it's an ordinary module-level
constant, easy to revisit.

Deliberately request-time, not rollup-backed (CLAUDE.md's rollup mandate) -- the same "bounded,
occasional diagnostic lookup" exception `vo2max_analysis.py`/`threshold_analysis.py` already
establish. Which race this even applies to can change day to day (a new nearer race gets added,
an old one passes), so there's no stable identity for a rollup row to accumulate against; the
whole computation, history included, is cheap enough (two queries over each window, all
bucketing/weighting done in Python) to redo on every request.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise

from sqlalchemy import Connection, and_, select

from perseverer.db.schema import activity, planned_race
from perseverer.planned_races import predicted_duration_s_for_distance

# (distance_m, target_m) anchor pairs -- see this module's own docstring for why these are
# recreational/intermediate-level targets, not an advanced/competitive baseline, and why they're
# independently set per distance rather than derived from each other via one shared ratio (the
# real long-run-as-%-of-weekly-volume relationship itself varies by athlete level, from roughly
# 25% at high weekly volumes to over 40% at recreational volumes -- there's no single ratio that
# stays honest across the whole range).
_WEEKLY_TARGET_ANCHORS: list[tuple[float, float]] = [
    (5_000.0, 25_000.0),
    (10_000.0, 32_000.0),
    (21_097.5, 40_000.0),
    (42_195.0, 55_000.0),
]
_LONG_RUN_TARGET_ANCHORS: list[tuple[float, float]] = [
    (5_000.0, 8_000.0),
    (10_000.0, 12_000.0),
    (21_097.5, 16_000.0),
    (42_195.0, 29_000.0),
]

WEEKLY_DISTANCE_WINDOW_DAYS = 182
WEEKLY_DISTANCE_HALF_LIFE_DAYS = 28.0
LONG_RUN_WINDOW_DAYS = 70
LONG_RUN_HALF_LIFE_DAYS = 14.0

READINESS_WEEKLY_DISTANCE_WEIGHT = 0.6
READINESS_LONG_RUN_WEIGHT = 0.4

# One history point per week over the weekly-distance window -- fine enough to show real
# week-to-week movement without being noisy, and it lines up with the week boundaries the
# compliance calculation itself buckets by.
_HISTORY_STEP_DAYS = 7


def _interpolate(distance_m: float, anchors: list[tuple[float, float]]) -> float:
    """Log-linear interpolation between (distance, target) anchor pairs, clamped to the anchor
    endpoints -- never extrapolated below the shortest or beyond the longest anchor distance,
    same "honest rather than extrapolated" posture `predict_race_time_s`'s own search bounds
    already establish for this codebase's other race-distance-dependent formula."""
    if distance_m <= anchors[0][0]:
        return anchors[0][1]
    if distance_m >= anchors[-1][0]:
        return anchors[-1][1]
    log_d = math.log(distance_m)
    for (x0, y0), (x1, y1) in pairwise(anchors):
        if x0 <= distance_m <= x1:
            t = (log_d - math.log(x0)) / (math.log(x1) - math.log(x0))
            return y0 + t * (y1 - y0)
    return anchors[-1][1]  # unreachable -- the two clamps above cover every other case


def weekly_distance_target_m(race_distance_m: float) -> float:
    return _interpolate(race_distance_m, _WEEKLY_TARGET_ANCHORS)


def long_run_target_m(race_distance_m: float) -> float:
    return _interpolate(race_distance_m, _LONG_RUN_TARGET_ANCHORS)


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday, same week-start convention as email_reports.py


def _weighted_compliance(
    weekly_values: dict[date, float],
    *,
    target_m: float,
    as_of: date,
    window_days: int,
    half_life_days: float,
) -> float:
    """Recency-weighted mean of `min(value/target, 1.0)` across every week in
    `[as_of - window_days, as_of]`, weighted by `0.5 ** (days_ago / half_life_days)`. A week with
    no entry in `weekly_values` (nothing recorded, or not yet in the window) contributes 0.0 at
    its own full weight -- a week that genuinely had no qualifying running is genuinely 0%
    compliant for that week, not an unknown to be excluded from the average. Returns 0.0 (never
    a fabricated better number) when the window has no weeks at all to weight, which only happens
    if `window_days` is misconfigured to be shorter than one week."""
    window_start = as_of - timedelta(days=window_days)
    weight_sum = 0.0
    weighted_total = 0.0
    w = _week_start(window_start)
    while w <= as_of:
        days_ago = (as_of - w).days
        weight = 0.5 ** (days_ago / half_life_days)
        value = weekly_values.get(w, 0.0)
        weighted_total += weight * min(value / target_m, 1.0)
        weight_sum += weight
        w += timedelta(days=7)
    return weighted_total / weight_sum if weight_sum > 0 else 0.0


@dataclass(frozen=True)
class ReadinessPoint:
    as_of: date
    weekly_distance_compliance: float  # 0..1
    long_run_compliance: float  # 0..1
    readiness: float  # 0..1, the weighted combination of the two above


@dataclass(frozen=True)
class WeekValue:
    week_start: date
    distance_m: float  # 0.0 (never omitted) for a week with nothing recorded


@dataclass(frozen=True)
class RaceReadiness:
    race_id: int
    race_name: str
    race_local_date: str
    race_distance_m: float
    weekly_distance_target_m: float
    long_run_target_m: float
    current: ReadinessPoint
    predicted_duration_s: float | None  # reused VDOT prediction; None for a non-standard distance
    history: list[ReadinessPoint] = field(default_factory=list)
    # One entry per Monday-start week, oldest first, covering the full weekly-distance/long-run
    # windows respectively -- the actual realized numbers each dedicated chart plots a target
    # line against, distinct from `history`'s own already-weighted/combined percentages.
    weekly_distance_series: list[WeekValue] = field(default_factory=list)
    long_run_series: list[WeekValue] = field(default_factory=list)


def _nearest_upcoming_race(conn: Connection, athlete_id: str, as_of: date) -> int | None:
    row = conn.execute(
        select(planned_race.c.id)
        .where(
            planned_race.c.athlete_id == athlete_id,
            planned_race.c.sport == "running",
            planned_race.c.local_date >= as_of.isoformat(),
        )
        .order_by(planned_race.c.local_date, planned_race.c.id)
        .limit(1)
    ).scalar_one_or_none()
    return row


def _weekly_running_totals(
    conn: Connection, athlete_id: str, start: date, end: date
) -> dict[date, float]:
    """One entry per Monday-start week with any running recorded in `[start, end]`, summing
    `distance_m` -- weeks with no running simply have no entry (see `_weighted_compliance`'s own
    docstring for why that's treated as 0, not excluded)."""
    rows = conn.execute(
        select(activity.c.local_date, activity.c.distance_m).where(
            and_(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport == "running",
                activity.c.local_date >= start.isoformat(),
                activity.c.local_date <= end.isoformat(),
                activity.c.distance_m.is_not(None),
            )
        )
    ).fetchall()
    totals: dict[date, float] = {}
    for r in rows:
        week = _week_start(date.fromisoformat(r.local_date))
        totals[week] = totals.get(week, 0.0) + r.distance_m
    return totals


def _weekly_longest_run(
    conn: Connection, athlete_id: str, start: date, end: date
) -> dict[date, float]:
    """One entry per Monday-start week with any running recorded in `[start, end]`, the single
    longest individual run that week -- the "long run" this app has no separate tag for, so the
    week's own longest effort stands in for it (a real long run is, definitionally, the longest
    run of its week)."""
    rows = conn.execute(
        select(activity.c.local_date, activity.c.distance_m).where(
            and_(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.sport == "running",
                activity.c.local_date >= start.isoformat(),
                activity.c.local_date <= end.isoformat(),
                activity.c.distance_m.is_not(None),
            )
        )
    ).fetchall()
    longest: dict[date, float] = {}
    for r in rows:
        week = _week_start(date.fromisoformat(r.local_date))
        longest[week] = max(longest.get(week, 0.0), r.distance_m)
    return longest


def _combine(weekly_distance_compliance: float, long_run_compliance: float) -> float:
    return (
        READINESS_WEEKLY_DISTANCE_WEIGHT * weekly_distance_compliance
        + READINESS_LONG_RUN_WEIGHT * long_run_compliance
    )


def _dense_weekly_series(
    weekly_values: dict[date, float], *, as_of: date, window_days: int
) -> list[WeekValue]:
    """Every Monday-start week in `[as_of - window_days, as_of]`, oldest first, `0.0` (never
    omitted) for a week with nothing in `weekly_values` -- the actual realized numbers a
    dedicated chart plots bars for, as opposed to `_weighted_compliance`'s own recency-weighted
    single figure."""
    series = []
    w = _week_start(as_of - timedelta(days=window_days))
    while w <= as_of:
        series.append(WeekValue(week_start=w, distance_m=weekly_values.get(w, 0.0)))
        w += timedelta(days=7)
    return series


def compute_race_readiness(
    conn: Connection, *, athlete_id: str, as_of: date, race_id: int | None = None
) -> RaceReadiness | None:
    """`race_id` defaults to the athlete's own nearest upcoming running race (`local_date >=
    as_of`); returns `None` (never fabricated) when no such race exists, or `race_id` doesn't
    belong to this athlete."""
    resolved_race_id = (
        race_id if race_id is not None else _nearest_upcoming_race(conn, athlete_id, as_of)
    )
    if resolved_race_id is None:
        return None
    row = conn.execute(
        select(planned_race).where(
            planned_race.c.id == resolved_race_id, planned_race.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        return None

    weekly_target = weekly_distance_target_m(row.distance_m)
    long_run_target = long_run_target_m(row.distance_m)

    # Fetched once, over the longer of the two windows plus enough lead time to cover every
    # historical `as_of` point's own lookback -- bucketed/weighted per as_of in Python below,
    # rather than one DB round trip per history point.
    fetch_start = as_of - timedelta(days=WEEKLY_DISTANCE_WINDOW_DAYS * 2)
    weekly_totals = _weekly_running_totals(conn, athlete_id, fetch_start, as_of)
    longest_runs = _weekly_longest_run(conn, athlete_id, fetch_start, as_of)

    def _point(point_as_of: date) -> ReadinessPoint:
        weekly_compliance = _weighted_compliance(
            weekly_totals,
            target_m=weekly_target,
            as_of=point_as_of,
            window_days=WEEKLY_DISTANCE_WINDOW_DAYS,
            half_life_days=WEEKLY_DISTANCE_HALF_LIFE_DAYS,
        )
        long_run_compliance = _weighted_compliance(
            longest_runs,
            target_m=long_run_target,
            as_of=point_as_of,
            window_days=LONG_RUN_WINDOW_DAYS,
            half_life_days=LONG_RUN_HALF_LIFE_DAYS,
        )
        return ReadinessPoint(
            as_of=point_as_of,
            weekly_distance_compliance=weekly_compliance,
            long_run_compliance=long_run_compliance,
            readiness=_combine(weekly_compliance, long_run_compliance),
        )

    history = []
    history_start = as_of - timedelta(days=WEEKLY_DISTANCE_WINDOW_DAYS)
    d = history_start
    while d <= as_of:
        history.append(_point(d))
        d += timedelta(days=_HISTORY_STEP_DAYS)
    if history[-1].as_of != as_of:
        history.append(_point(as_of))

    return RaceReadiness(
        race_id=row.id,
        race_name=row.name,
        race_local_date=row.local_date,
        race_distance_m=row.distance_m,
        weekly_distance_target_m=weekly_target,
        long_run_target_m=long_run_target,
        current=history[-1],
        predicted_duration_s=predicted_duration_s_for_distance(
            conn, athlete_id=athlete_id, distance_m=row.distance_m
        ),
        history=history,
        weekly_distance_series=_dense_weekly_series(
            weekly_totals, as_of=as_of, window_days=WEEKLY_DISTANCE_WINDOW_DAYS
        ),
        long_run_series=_dense_weekly_series(
            longest_runs, as_of=as_of, window_days=LONG_RUN_WINDOW_DAYS
        ),
    )
