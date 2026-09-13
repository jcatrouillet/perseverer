"""Point-in-time factor breakdown for the athlete's own anaerobic and aerobic threshold pace/HR
(`performance_daily_rollup.threshold_pace_s_per_km`/`threshold_hr_bpm` and
`aerobic_threshold_pace_s_per_km`/`aerobic_threshold_hr_bpm` -- see `performance_rollup.py`'s own
docstring and `vdot.py`'s `THRESHOLD_VO2MAX_FRACTION`/`AEROBIC_THRESHOLD_VO2MAX_FRACTION` for the
model). Both threshold PACES are pure functions of the same `rolling_vdot`, so "which workout led
to the current threshold pace" is exactly `vo2max_analysis.py`'s own driving-activity answer,
reused here rather than re-derived. Both threshold HRs additionally need "which workout(s) led to
this HR" of their own: the empirical path is a median over several qualifying runs (not one
driving run), and the fallback path is driven by whichever activity set `max_hr_bpm`.

Deliberately request-time, not rollup-backed, for the same reason `vo2max_analysis.py` is: the
window this scans is small (`performance_rollup.THRESHOLD_HR_WINDOW_DAYS`/`MAX_HR_WINDOW_DAYS` of
the athlete's own activities), the same "bounded, occasional diagnostic lookup" exception
`/activities/needs-trim` already establishes to the rollup mandate (CLAUDE.md). The scalar
threshold values themselves are read straight from `performance_daily_rollup` for `as_of` (not
recomputed) -- only the "which activities" breakdown is derived here, so this can never disagree
with what `GET /performance` itself reports for the same date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Connection, select

from perseverer.db.schema import activity, activity_metric, performance_daily_rollup
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.performance_rollup import (
    AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR,
    AVG_HR_METRIC_KEYS,
    MAX_HR_METRIC_KEYS,
    MAX_HR_WINDOW_DAYS,
    THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR,
    THRESHOLD_HR_WINDOW_DAYS,
    THRESHOLD_PACE_TOLERANCE,
    priority_merge,
)
from perseverer.vo2max_analysis import Vo2maxFactorAnalysis, compute_vo2max_factor_analysis


@dataclass
class ActivityRef:
    activity_id: str
    local_date: str
    name: str | None
    sport: str
    distance_m: float | None
    duration_s: float | None


@dataclass
class ThresholdHrContributor(ActivityRef):
    pace_s_per_km: float
    avg_hr_bpm: float
    is_median: bool


@dataclass
class ThresholdHrBreakdown:
    threshold_hr_bpm: float | None
    threshold_hr_source: str | None
    reference_pace_s_per_km: float | None
    contributors: list[ThresholdHrContributor]
    max_hr_driving_activity: ActivityRef | None
    missing: list[str]


@dataclass
class ThresholdFactorAnalysis:
    as_of: str
    vo2max: Vo2maxFactorAnalysis
    anaerobic_threshold_pace_s_per_km: float | None
    aerobic_threshold_pace_s_per_km: float | None
    anaerobic_threshold_hr: ThresholdHrBreakdown
    aerobic_threshold_hr: ThresholdHrBreakdown
    max_hr_bpm: float | None
    max_hr_source: str | None


def _activity_rows_for(
    conn: Connection, *, athlete_id: str, activity_ids: list[str]
) -> dict[str, tuple[str, str | None, str, float | None, float | None]]:
    """(local_date, name, sport, distance_m, duration_s) per activity_id, for exactly the ids
    already selected by a prior metric query -- never a second scan of the whole table."""
    if not activity_ids:
        return {}
    rows = conn.execute(
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.name,
            activity.c.sport,
            activity.c.distance_m,
            activity.c.moving_duration_s,
        ).where(activity.c.athlete_id == athlete_id, activity.c.id.in_(activity_ids))
    ).fetchall()
    return {r.id: (r.local_date, r.name, r.sport, r.distance_m, r.moving_duration_s) for r in rows}


def _threshold_hr_breakdown(
    conn: Connection,
    *,
    athlete_id: str,
    as_of: date,
    reference_pace_s_per_km: float | None,
    threshold_hr_bpm: float | None,
    threshold_hr_source: str | None,
    max_hr_bpm: float | None,
    max_hr_source: str | None,
    fallback_fraction: float,
    threshold_window_days: int,
) -> ThresholdHrBreakdown:
    if reference_pace_s_per_km is None:
        return ThresholdHrBreakdown(
            threshold_hr_bpm=None,
            threshold_hr_source=None,
            reference_pace_s_per_km=None,
            contributors=[],
            max_hr_driving_activity=None,
            missing=[
                "No threshold pace to measure runs against yet -- see the VO2max factor "
                "analysis above for what's needed first."
            ],
        )

    window_start = as_of - timedelta(days=threshold_window_days - 1)
    lo = reference_pace_s_per_km * (1 - THRESHOLD_PACE_TOLERANCE)
    hi = reference_pace_s_per_km * (1 + THRESHOLD_PACE_TOLERANCE)

    # Every VDOT-eligible run in the window (VDOT is only ever computed for sport == "running",
    # see performance.py::refresh_vdot, so no separate sport filter is needed here -- same
    # implicit restriction refresh_performance_rollup's own threshold_candidates relies on).
    vdot_rows = conn.execute(
        select(activity_metric.c.activity_id)
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
    ).fetchall()
    candidate_ids = [r.activity_id for r in vdot_rows]

    avg_gap_by_activity: dict[str, float] = {}
    avg_hr_by_activity: dict[str, float] = {}
    if candidate_ids:
        avg_gap_by_activity = {
            r.activity_id: r.value_num
            for r in conn.execute(
                select(activity_metric.c.activity_id, activity_metric.c.value_num).where(
                    activity_metric.c.athlete_id == athlete_id,
                    activity_metric.c.metric_key == AVG_GAP_METRIC_KEY,
                    activity_metric.c.activity_id.in_(candidate_ids),
                )
            )
        }
        avg_hr_raw = conn.execute(
            select(
                activity_metric.c.activity_id,
                activity_metric.c.metric_key,
                activity_metric.c.value_num,
            ).where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.metric_key.in_(AVG_HR_METRIC_KEYS),
                activity_metric.c.activity_id.in_(candidate_ids),
            )
        ).fetchall()
        avg_hr_by_activity = priority_merge(
            [(r.activity_id, r.metric_key, r.value_num) for r in avg_hr_raw], AVG_HR_METRIC_KEYS
        )

    activity_details = _activity_rows_for(conn, athlete_id=athlete_id, activity_ids=candidate_ids)

    qualifying: list[ThresholdHrContributor] = []
    for activity_id in candidate_ids:
        gap_speed = avg_gap_by_activity.get(activity_id)
        avg_hr = avg_hr_by_activity.get(activity_id)
        if gap_speed is None or gap_speed <= 0 or avg_hr is None:
            continue
        pace = 1000.0 / gap_speed
        if not (lo <= pace <= hi):
            continue
        local_date, name, sport, distance_m, duration_s = activity_details[activity_id]
        qualifying.append(
            ThresholdHrContributor(
                activity_id=activity_id,
                local_date=local_date,
                name=name,
                sport=sport,
                distance_m=distance_m,
                duration_s=duration_s,
                pace_s_per_km=pace,
                avg_hr_bpm=avg_hr,
                is_median=False,
            )
        )
    qualifying.sort(key=lambda c: c.avg_hr_bpm)

    missing: list[str] = []
    if threshold_hr_source == "empirical":
        n = len(qualifying)
        mid_indices = [n // 2] if n % 2 == 1 else [n // 2 - 1, n // 2]
        for i in mid_indices:
            qualifying[i].is_median = True
    elif threshold_hr_source == "fallback":
        if qualifying:
            missing.append(
                f"Only {len(qualifying)} qualifying run(s) near this pace in the last "
                f"{threshold_window_days} days (3 needed for an empirical read) -- using "
                f"{fallback_fraction:.1%} of max HR instead."
            )
        else:
            missing.append(
                f"No runs recorded near this pace in the last {threshold_window_days} days -- "
                f"using {fallback_fraction:.1%} of max HR instead."
            )
    else:
        missing.append("No max HR available yet to fall back to either.")

    max_hr_driving_activity: ActivityRef | None = None
    if threshold_hr_source == "fallback" and max_hr_source == "empirical":
        max_hr_window_start = as_of - timedelta(days=MAX_HR_WINDOW_DAYS - 1)
        max_hr_raw = conn.execute(
            select(
                activity_metric.c.activity_id,
                activity.c.local_date,
                activity_metric.c.metric_key,
                activity_metric.c.value_num,
            )
            .select_from(
                activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id)
            )
            .where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.metric_key.in_(MAX_HR_METRIC_KEYS),
                activity.c.deleted_at.is_(None),
                activity.c.local_date >= max_hr_window_start.isoformat(),
                activity.c.local_date <= as_of.isoformat(),
            )
        ).fetchall()
        merged = priority_merge(
            [(r.activity_id, r.metric_key, r.value_num) for r in max_hr_raw], MAX_HR_METRIC_KEYS
        )
        if merged:
            driving_id = max(merged, key=lambda aid: merged[aid])
            details = _activity_rows_for(conn, athlete_id=athlete_id, activity_ids=[driving_id])
            if driving_id in details:
                local_date, name, sport, distance_m, duration_s = details[driving_id]
                max_hr_driving_activity = ActivityRef(
                    activity_id=driving_id,
                    local_date=local_date,
                    name=name,
                    sport=sport,
                    distance_m=distance_m,
                    duration_s=duration_s,
                )

    return ThresholdHrBreakdown(
        threshold_hr_bpm=threshold_hr_bpm,
        threshold_hr_source=threshold_hr_source,
        reference_pace_s_per_km=reference_pace_s_per_km,
        contributors=qualifying,
        max_hr_driving_activity=max_hr_driving_activity,
        missing=missing,
    )


def compute_threshold_factor_analysis(
    conn: Connection, *, athlete_id: str, as_of: date
) -> ThresholdFactorAnalysis:
    vo2max = compute_vo2max_factor_analysis(conn, athlete_id=athlete_id, as_of=as_of)

    row = conn.execute(
        select(performance_daily_rollup).where(
            performance_daily_rollup.c.athlete_id == athlete_id,
            performance_daily_rollup.c.local_date == as_of.isoformat(),
        )
    ).fetchone()

    anaerobic_pace = row.threshold_pace_s_per_km if row is not None else None
    anaerobic_hr = row.threshold_hr_bpm if row is not None else None
    anaerobic_hr_source = row.threshold_hr_source if row is not None else None
    aerobic_pace = row.aerobic_threshold_pace_s_per_km if row is not None else None
    aerobic_hr = row.aerobic_threshold_hr_bpm if row is not None else None
    aerobic_hr_source = row.aerobic_threshold_hr_source if row is not None else None
    max_hr_bpm = row.max_hr_bpm if row is not None else None
    max_hr_source = row.max_hr_source if row is not None else None

    anaerobic_breakdown = _threshold_hr_breakdown(
        conn,
        athlete_id=athlete_id,
        as_of=as_of,
        reference_pace_s_per_km=anaerobic_pace,
        threshold_hr_bpm=anaerobic_hr,
        threshold_hr_source=anaerobic_hr_source,
        max_hr_bpm=max_hr_bpm,
        max_hr_source=max_hr_source,
        fallback_fraction=THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR,
        threshold_window_days=THRESHOLD_HR_WINDOW_DAYS,
    )
    aerobic_breakdown = _threshold_hr_breakdown(
        conn,
        athlete_id=athlete_id,
        as_of=as_of,
        reference_pace_s_per_km=aerobic_pace,
        threshold_hr_bpm=aerobic_hr,
        threshold_hr_source=aerobic_hr_source,
        max_hr_bpm=max_hr_bpm,
        max_hr_source=max_hr_source,
        fallback_fraction=AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR,
        threshold_window_days=THRESHOLD_HR_WINDOW_DAYS,
    )

    return ThresholdFactorAnalysis(
        as_of=as_of.isoformat(),
        vo2max=vo2max,
        anaerobic_threshold_pace_s_per_km=anaerobic_pace,
        aerobic_threshold_pace_s_per_km=aerobic_pace,
        anaerobic_threshold_hr=anaerobic_breakdown,
        aerobic_threshold_hr=aerobic_breakdown,
        max_hr_bpm=max_hr_bpm,
        max_hr_source=max_hr_source,
    )
