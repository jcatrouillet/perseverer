"""Point-in-time, per-activity insights: how does THIS activity compare to the athlete's own
past -- never future -- activities. Running and cycling activities compose the existing
athlete-wide rules
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

`_has_real_comparison` drops a period record whose period holds fewer than
`_MIN_COMPARISON_POOL` activities of the same sport with a value for it: "hottest of 2" or "the
longest of the only ride this month" reads as an invented-sounding claim, same underlying issue
as a 1-day "streak." It first applied to temperature only (weather is sparse: not every activity
has a location to fetch it for), and once caught a run flagged both "Hottest run in the last 6
months" and "Coldest run in the last 12 months" with barely any weather in either window. Rides
showed the same problem with every record (a ride often is the only one in the last 30 days, so
it was at once the highest and the lowest average heart rate), so every effort record is now
checked, as is "fastest N km in the period" (counting only activities in the same km band). The
check runs on the period each record finally claims, after `_dedupe_widest_window` and
`_upgrade_to_all_time`: the widest period has the largest pool, so a record that holds over a
well-populated year or all history is never lost to a sparse last month. "All-time best N km"
is not tied to a period and is not checked.

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
from perseverer.insights.rules_pb import (
    compute_pb_insights,
    compute_window_best_insights,
    floor_km_band,
)
from perseverer.insights.rules_streaks import current_streak
from perseverer.insights.types import Insight, InsightActivity

_DIMENSION_BY_KEY = {d.key: d for d in DIMENSIONS}
_WINDOW_DAYS_BY_NAME = dict(WINDOWS)

_FIXED_WINDOW_DAYS: dict[str, int] = {"30d": 30, "90d": 90, "180d": 180, "365d": 365}

_ALL_TIME_WINDOW = "all_time"

# Below this, a "run streak" is just restating "you ran today" -- not shown at all.
_MIN_NOTABLE_STREAK_DAYS = 4

# Below this many same-sport activities with a value in the period, a record isn't a real
# comparison -- see the module docstring.
_MIN_COMPARISON_POOL = 3

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
_RUN_EFFORT_PHRASES: dict[str, str] = {
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
    "max_altitude": "Highest point reached",
    "calories": "Most calories burned",
    "start_earliest": "Earliest start",
    "start_latest": "Latest start",
    "temperature_high": "Hottest run",
    "temperature_low": "Coldest run",
    "start_fast": "Fastest start",
    "start_slow": "Slowest start",
    "finish_fast": "Fastest finish",
    "finish_slow": "Slowest finish",
    "negative_km_splits": "Most negative km splits",
}

# A ride's own wording for the same records. Rides are only ever ranked against rides (every
# effort dimension is sport-scoped), so "Fastest average speed" is the ride counterpart of a
# run's "Fastest pace": the same moving-time ranking, said the way a cyclist reads it.
_RIDE_EFFORT_PHRASES: dict[str, str] = {
    **_RUN_EFFORT_PHRASES,
    "distance": "Longest ride",
    "duration": "Longest ride by time",
    "pace": "Fastest average speed",
    "temperature_high": "Hottest ride",
    "temperature_low": "Coldest ride",
}

_EFFORT_PHRASES_BY_FAMILY: dict[str, dict[str, str]] = {"ride": _RIDE_EFFORT_PHRASES}

# The families with their own streak: a run counts only runs, a ride only rides.
_STREAK_NOUNS: dict[str, str] = {"run": "run", "ride": "ride"}


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
        "attempted_grade",
        "Highest attempted grade",
        "high",
        lambda a: a.climb_max_attempted_grade,
        "V-grade",
    ),
    _ClimbDimension(
        "completed_grade",
        "Highest completed grade",
        "high",
        lambda a: a.climb_max_completed_grade,
        "V-grade",
    ),
    _ClimbDimension("climb_time", "Longest climb time", "high", lambda a: a.climb_time_s, "s"),
    _ClimbDimension("max_hr_high", "Highest heart rate", "high", lambda a: a.max_hr, "bpm"),
    _ClimbDimension("max_hr_low", "Lowest peak heart rate", "low", lambda a: a.max_hr, "bpm"),
    _ClimbDimension("avg_hr_high", "Highest average heart rate", "high", lambda a: a.avg_hr, "bpm"),
    _ClimbDimension("avg_hr_low", "Lowest average heart rate", "low", lambda a: a.avg_hr, "bpm"),
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
    return [a for a in bounded_activities if start.isoformat() <= a.local_date <= as_of.isoformat()]


def _has_real_comparison(
    insight: Insight, bounded_activities: list[InsightActivity], as_of: date
) -> bool:
    candidates = _window_activities(bounded_activities, insight.window, as_of)
    if insight.sport_family is not None:
        candidates = [a for a in candidates if a.sport_family == insight.sport_family]
    if insight.kind == "window_best":
        # subject_key is "window_best:{family}:{N} km": compare within that km band only.
        band_km = int(insight.subject_key.split(":", 2)[-1].split(" ", 1)[0])
        count = sum(
            1
            for a in candidates
            if a.distance_m is not None and floor_km_band(a.distance_m) == band_km
        )
        return count >= _MIN_COMPARISON_POOL
    dim = _DIMENSION_BY_KEY.get(insight.subject_key.split(":", 1)[0])
    if dim is None:
        return True
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
        phrases = _EFFORT_PHRASES_BY_FAMILY.get(insight.sport_family or "", _RUN_EFFORT_PHRASES)
        phrase = phrases.get(dim_key, insight.title)
        period = _WINDOW_LABELS.get(insight.window, "")
        relabelled = replace(insight, title=f"{phrase} {period}".strip())
        if insight.sport_family == "ride" and dim_key in ("cadence", "max_cadence"):
            # Pedalling cadence, not steps (rules_efforts.py's shared dimension says spm).
            relabelled = replace(relabelled, detail={**relabelled.detail, "unit": "rpm"})
        return relabelled
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
        noun = _STREAK_NOUNS.get(insight.sport_family or "", "run")
        return replace(insight, title=f"{days}-day {noun} streak")
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
    if any(a.id == activity_id and a.climb_route_count is not None for a in bounded_activities):
        return _compute_climbing_activity_insights(activity_id, bounded_activities, as_of)

    effort_all = compute_effort_insights(bounded_activities, as_of)
    effort = [i for i in effort_all if i.activity_id == activity_id]
    pb_all = compute_pb_insights(bounded_activities, as_of)
    pb = [i for i in pb_all if i.activity_id == activity_id]
    window_best_all = compute_window_best_insights(bounded_activities, as_of)
    window_best = [i for i in window_best_all if i.activity_id == activity_id]

    effort_deduped = _dedupe_widest_window(effort, as_of)
    effort_final = [
        upgraded
        for i in effort_deduped
        if _has_real_comparison(
            upgraded := _upgrade_to_all_time(i, activity_id, bounded_activities),
            bounded_activities,
            as_of,
        )
    ]
    window_best_final = [
        i
        for i in _dedupe_widest_window(window_best, as_of)
        if _has_real_comparison(i, bounded_activities, as_of)
    ]
    results = effort_final + _dedupe_widest_window(pb, as_of) + window_best_final

    # Restricted to this activity's own sport -- a "run streak" (consecutive days with a run) on
    # a run, a "ride streak" on a ride, never the athlete-wide engine's cross-sport "activity
    # streak", and none for other sports. Not filtered by activity_id: this describes the
    # athlete's state as of `as_of`, not a fact tied to one specific activity. Below
    # _MIN_NOTABLE_STREAK_DAYS it's not a real insight -- every run is trivially the start of a
    # "1-day streak," and that's just restating "you ran today."
    family = next((a.sport_family for a in bounded_activities if a.id == activity_id), None)
    if family in _STREAK_NOUNS:
        same_sport = [a for a in bounded_activities if a.sport_family == family]
        streak = current_streak(same_sport, as_of)
        if (
            streak is not None
            and streak.value_num is not None
            and streak.value_num >= _MIN_NOTABLE_STREAK_DAYS
        ):
            results.append(replace(streak, sport_family=family))

    return [_relabel(i) for i in results]
