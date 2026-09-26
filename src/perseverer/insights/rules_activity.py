"""Point-in-time, per-activity insights: how does THIS activity compare to the athlete's own
past -- never future -- activities. Running activities compose the existing athlete-wide rules
(`compute_effort_insights`, `compute_pb_insights`, `compute_window_best_insights`,
`rules_streaks.current_streak`) unchanged rather than a new comparison algorithm: calling them
with a candidate pool already bounded to
`local_date <= as_of` (the caller's job -- see api/routers/activities.py::get_activity_insights)
makes "never look into the future" a property of the *input*, not something this module has to
re-verify itself. Same look-ahead-safety shape as `ActivityContextOut.recent`
(api/routers/activities.py), just reused for a different, request-time-computed view.

Filters each rule's output down to insights that are actually *about* this one activity (its
`activity_id`), then dedupes across the five standard windows: if this run is the longest in the
last 365 days it's necessarily also the longest in every shorter window ending on the same date
(each window's candidate set is a subset of the next-wider one, and this activity's own date
falls inside every window since `as_of` is always this activity's own date) -- so only the widest
true claim is kept, not four near-identical repeats.

`compute_effort_insights`'s own windows top out at 365d -- there's no "all-time" window in that
table, so a genuinely all-time-longest run (e.g. a first marathon, with the athlete's full
history spanning years) would otherwise only ever be labelled "in the last 12 months", which is
true but misleadingly narrow. `_upgrade_to_all_time` re-checks each surviving effort insight
against the *entire* bounded pool (no window at all, just `local_date <= as_of`) using the same
`find_extreme` function -- if this activity is still the winner with no window restriction, its
window is promoted to the "all_time" sentinel so `_relabel` below says "ever" instead. PB
insights don't need this: `compute_pb_insights`'s own "all-time best" check is already unbounded
by window (see rules_pb.py), so its title is already correctly "All-time best {label}".

`_has_real_comparison` drops a *temperature* effort insight whose window has fewer than
`_MIN_COMPARISON_POOL` activities with a weather value -- confirmed against real data that this
matters: a run can legitimately be flagged both "Hottest run in the last 6 months" and "Coldest
run in the last 12 months" simultaneously (max-temperature and min-temperature are different
fields, so it's not a contradiction in the data), but if only one or two activities in that
window even have weather data at all, "hottest of 2" reads as an invented-sounding claim, same
underlying issue as a 1-day "streak." Scoped to temperature only (`_POOL_CHECKED_DIMENSIONS`) --
weather is the one dimension with genuinely sparse coverage (not every activity has GPS/location
to fetch it for); distance, pace, heart rate, elevation, and start time are recorded on
essentially every activity, so the same population check would just suppress ordinary, real
"longest run" claims for a normal-sized history.

Every surviving running Insight's title is then rewritten (`_relabel`) into a period-explicit
phrase -- "Longest run in the last 12 months" or "Longest run ever" rather than the athlete-wide
engine's generic "Longest distance (run)" -- so the panel never leaves the athlete guessing
which window ("last month? last year? ever?") a claim is about. Bouldering uses the session's
route splits directly for its own record dimensions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date

from perseverer.insights.rules_efforts import (
    DIMENSIONS,
    WINDOWS,
    compute_effort_insights,
    find_extreme,
    window_start_date,
)
from perseverer.insights.rules_pb import compute_pb_insights, compute_window_best_insights
from perseverer.insights.rules_streaks import current_streak
from perseverer.insights.types import Insight, InsightActivity

_DIMENSION_BY_KEY = {d.key: d for d in DIMENSIONS}
_WINDOW_DAYS_BY_NAME = dict(WINDOWS)

_FIXED_WINDOW_DAYS: dict[str, int] = {"30d": 30, "90d": 90, "180d": 180, "365d": 365}

_ALL_TIME_WINDOW = "all_time"

# Below this, a "run streak" is just restating "you ran today" -- not shown at all.
_MIN_NOTABLE_STREAK_DAYS = 4

# Below this many activities-with-a-value in the window, a temperature extreme isn't a real
# comparison -- see module docstring for the real example that motivated this (a run flagged
# both hottest-in-6-months and coldest-in-12-months because almost nothing else in either window
# had weather data at all). Only applied to _POOL_CHECKED_DIMENSIONS.
_MIN_COMPARISON_POOL = 3
_POOL_CHECKED_DIMENSIONS = frozenset({"temperature_high", "temperature_low"})

_WINDOW_LABELS: dict[str, str] = {
    "30d": "in the last 30 days",
    "90d": "in the last 90 days",
    "180d": "in the last 6 months",
    "year": "so far this year",
    "365d": "in the last 12 months",
    _ALL_TIME_WINDOW: "ever",
}

# Keyed by the effort dimension's own `key` (rules_efforts.py::_Dimension), not its title string
# -- decoupled from that module's own "{title} ({family})" formatting, which this module discards
# entirely in favour of a period-explicit phrasing (see module docstring).
_EFFORT_PHRASES: dict[str, str] = {
    "distance": "Longest run",
    "duration": "Longest run by time",
    "pace": "Fastest pace",
    "avg_hr_high": "Highest average heart rate",
    "avg_hr_low": "Lowest average heart rate",
    "max_hr_high": "Highest heart rate",
    "max_hr_low": "Lowest peak heart rate",
    "cadence": "Highest average cadence",
    "max_cadence": "Highest max cadence",
    "elevation_gain": "Most elevation gained",
    "elevation_loss": "Most elevation lost",
    "start_earliest": "Earliest start",
    "start_latest": "Latest start",
    "temperature_high": "Hottest run",
    "temperature_low": "Coldest run",
}


@dataclass(frozen=True)
class _ClimbDimension:
    key: str
    phrase: str
    direction: str  # "high" | "low"
    value_fn: Callable[[InsightActivity], float | None]
    unit: str


# These values describe one bouldering session, not the activity's generic distance/duration
# fields. An attempted grade includes every graded route: completing a route necessarily means
# attempting it first, while a higher failed route can still establish the attempted-grade best.
_CLIMB_DIMENSIONS: tuple[_ClimbDimension, ...] = (
    _ClimbDimension("route_count", "Most routes", "high", lambda a: a.climb_route_count, "routes"),
    _ClimbDimension(
        "attempted_grade", "Highest attempted grade", "high",
        lambda a: a.climb_max_attempted_grade, "V-grade",
    ),
    _ClimbDimension(
        "completed_grade", "Highest completed grade", "high",
        lambda a: a.climb_max_completed_grade, "V-grade",
    ),
    _ClimbDimension("climb_time", "Longest climb time", "high", lambda a: a.climb_time_s, "s"),
    _ClimbDimension("max_hr_high", "Highest heart rate", "high", lambda a: a.max_hr, "bpm"),
    _ClimbDimension("max_hr_low", "Lowest peak heart rate", "low", lambda a: a.max_hr, "bpm"),
    _ClimbDimension(
        "avg_hr_high", "Highest average heart rate", "high", lambda a: a.avg_hr, "bpm"
    ),
    _ClimbDimension(
        "avg_hr_low", "Lowest average heart rate", "low", lambda a: a.avg_hr, "bpm"
    ),
)

_CLIMB_WINDOWS: tuple[tuple[str, int | None], ...] = (
    ("30d", 30),
    ("90d", 90),
    ("year", None),
    (_ALL_TIME_WINDOW, None),
)


def _window_span_days(window: str, as_of: date) -> int:
    if window == _ALL_TIME_WINDOW:
        return 10**9  # always the widest -- see _upgrade_to_all_time
    if window == "year":
        return (as_of - date(as_of.year, 1, 1)).days
    return _FIXED_WINDOW_DAYS.get(window, 0)


def _dedupe_widest_window(insights: list[Insight], as_of: date) -> list[Insight]:
    best: dict[str, Insight] = {}
    for ins in insights:
        span = _window_span_days(ins.window, as_of)
        current = best.get(ins.subject_key)
        if current is None or span > _window_span_days(current.window, as_of):
            best[ins.subject_key] = ins
    return list(best.values())


def _window_activities(
    bounded_activities: list[InsightActivity], window: str, as_of: date
) -> list[InsightActivity]:
    if window == _ALL_TIME_WINDOW:
        return bounded_activities
    start = window_start_date(window, _WINDOW_DAYS_BY_NAME.get(window), as_of)
    return [
        a for a in bounded_activities if start.isoformat() <= a.local_date <= as_of.isoformat()
    ]


def _has_real_comparison(
    insight: Insight, bounded_activities: list[InsightActivity], as_of: date
) -> bool:
    dim_key = insight.subject_key.split(":", 1)[0]
    if dim_key not in _POOL_CHECKED_DIMENSIONS:
        return True
    dim = _DIMENSION_BY_KEY.get(dim_key)
    if dim is None:
        return True
    candidates = _window_activities(bounded_activities, insight.window, as_of)
    if dim.sport_scoped and insight.sport_family is not None:
        candidates = [a for a in candidates if a.sport_family == insight.sport_family]
    count = sum(1 for a in candidates if dim.value_fn(a) is not None)
    return count >= _MIN_COMPARISON_POOL


def _upgrade_to_all_time(
    insight: Insight, activity_id: str, bounded_activities: list[InsightActivity]
) -> Insight:
    dim = _DIMENSION_BY_KEY.get(insight.subject_key.split(":", 1)[0])
    if dim is None:
        return insight
    winners = find_extreme(bounded_activities, dim, window=_ALL_TIME_WINDOW)
    if any(w.activity_id == activity_id for w in winners):
        return replace(insight, window=_ALL_TIME_WINDOW)
    return insight


def _relabel(insight: Insight) -> Insight:
    if insight.kind == "effort":
        dim_key = insight.subject_key.split(":", 1)[0]
        phrase = _EFFORT_PHRASES.get(dim_key, insight.title)
        period = _WINDOW_LABELS.get(insight.window, "")
        return replace(insight, title=f"{phrase} {period}".strip())
    if insight.kind == "pb":
        # subject_key is "pb:{family}:{label}" (rules_pb.py) -- label itself never contains a
        # colon (it's always "{N} km"), so splitting into at most 3 parts is exact.
        label = insight.subject_key.split(":", 2)[-1]
        return replace(insight, title=f"All-time best {label}")
    if insight.kind == "window_best":
        # subject_key is "window_best:{family}:{label}" -- same split shape as "pb" above.
        label = insight.subject_key.split(":", 2)[-1]
        period = _WINDOW_LABELS.get(insight.window, "")
        return replace(insight, title=f"Fastest {label} {period}".strip())
    if insight.kind == "streak":
        days = int(insight.value_num) if insight.value_num is not None else 0
        return replace(insight, title=f"{days}-day run streak")
    return insight


def _climbing_activities(activities: list[InsightActivity]) -> list[InsightActivity]:
    """Only sessions with a real graded route belong in bouldering comparisons.

    This is deliberately data-driven rather than based only on the sport label: routes are the
    source of truth for a bouldering session, and it keeps a rock-climbing activity with no
    bouldering splits out of these records.
    """
    return [a for a in activities if a.climb_route_count is not None]


def _climb_window_activities(
    activities: list[InsightActivity], window: str, days: int | None, as_of: date
) -> list[InsightActivity]:
    if window == _ALL_TIME_WINDOW:
        return activities
    start = window_start_date(window, days, as_of)
    return [a for a in activities if start.isoformat() <= a.local_date <= as_of.isoformat()]


def _climb_winner(
    activities: list[InsightActivity], dim: _ClimbDimension
) -> tuple[InsightActivity, float] | None:
    scored = [(a, value) for a in activities if (value := dim.value_fn(a)) is not None]
    if not scored:
        return None
    values = [value for _, value in scored]
    best_value = min(values) if dim.direction == "low" else max(values)
    # A tie is not a new record. The earliest session with the value owns the record; ordering
    # by start timestamp also makes this deterministic for the loader and direct rule tests.
    winners = [pair for pair in scored if pair[1] == best_value]
    return min(winners, key=lambda pair: (pair[0].start_time_utc, pair[0].id))


def _compute_climbing_activity_insights(
    activity_id: str, bounded_activities: list[InsightActivity], as_of: date
) -> list[Insight]:
    climbing = _climbing_activities(bounded_activities)
    if not any(a.id == activity_id for a in climbing):
        return []

    records: list[Insight] = []
    for window, days in _CLIMB_WINDOWS:
        candidates = _climb_window_activities(climbing, window, days, as_of)
        for dim in _CLIMB_DIMENSIONS:
            winner = _climb_winner(candidates, dim)
            if winner is None or winner[0].id != activity_id:
                continue
            period = _WINDOW_LABELS[window]
            records.append(
                Insight(
                    kind="climb_record",
                    window=window,
                    subject_key=f"climb:{dim.key}",
                    title=f"{dim.phrase} {period}",
                    detail={"activity_id": activity_id, "unit": dim.unit, "sport": "rock_climbing"},
                    value_num=winner[1],
                    sport_family="climb",
                    activity_id=activity_id,
                    local_date=winner[0].local_date,
                )
            )
    return _dedupe_widest_window(records, as_of)


def compute_activity_insights(
    activity_id: str, bounded_activities: list[InsightActivity], as_of: date
) -> list[Insight]:
    """`bounded_activities` must already exclude anything after `as_of` -- this function does not
    re-check dates itself, matching every other rule module's "pure function over an
    already-bounded list" contract."""
    # Bouldering has route-specific records. Do not run the generic effort rules for it: those
    # would describe a bouldering session as a "run" and include irrelevant distance metrics.
    if any(
        a.id == activity_id and a.climb_route_count is not None for a in bounded_activities
    ):
        return _compute_climbing_activity_insights(activity_id, bounded_activities, as_of)

    effort_all = compute_effort_insights(bounded_activities, as_of)
    effort = [i for i in effort_all if i.activity_id == activity_id]
    effort = [i for i in effort if _has_real_comparison(i, bounded_activities, as_of)]
    pb_all = compute_pb_insights(bounded_activities, as_of)
    pb = [i for i in pb_all if i.activity_id == activity_id]
    window_best_all = compute_window_best_insights(bounded_activities, as_of)
    window_best = [i for i in window_best_all if i.activity_id == activity_id]

    effort_deduped = _dedupe_widest_window(effort, as_of)
    effort_final = [
        _upgrade_to_all_time(i, activity_id, bounded_activities) for i in effort_deduped
    ]
    results = (
        effort_final
        + _dedupe_widest_window(pb, as_of)
        + _dedupe_widest_window(window_best, as_of)
    )

    # Restricted to running activities only -- a "run streak" (consecutive days with a run), not
    # the athlete-wide engine's cross-sport "activity streak". Not filtered by activity_id:
    # this describes the athlete's state as of `as_of`, not a fact tied to one specific activity.
    # Below _MIN_NOTABLE_STREAK_DAYS it's not a real insight -- every run is trivially the start
    # of a "1-day streak," and that's just restating "you ran today."
    running_activities = [a for a in bounded_activities if a.sport_family == "run"]
    streak = current_streak(running_activities, as_of)
    if (
        streak is not None
        and streak.value_num is not None
        and streak.value_num >= _MIN_NOTABLE_STREAK_DAYS
    ):
        results.append(streak)

    return [_relabel(i) for i in results]
