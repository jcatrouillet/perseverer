"""Independently-computed race-time predictions (5k/10k/half marathon/marathon) and current max
HR / threshold pace / threshold HR -- built on the same Daniels-Gilbert VDOT model vdot.py/
performance.py already use elsewhere in this codebase (see vdot.py's own docstring for the
race-time/threshold-pace math and why VDOT was chosen over Riegel's simpler formula, and why this
is an extension of one already-verified model rather than a second one). Deliberately never
Garmin's own precomputed `garmin.daily_race_predictions.*`/`garmin.daily_lactate_threshold.*`
fields -- those stay untouched and are shown alongside, never reconciled against this, the same
posture fitness.py already takes with Garmin's own Training Readiness (see that module's own
docstring for the precedent).

Full delete-and-reinsert per athlete per call, same precedent as
fitness.py::refresh_fitness_rollup: one row per calendar day from the earliest qualifying
activity through today, a single forward (causal) pass -- each day's rolling windows only ever
look backward in the sorted input lists, so a later activity never changes an earlier day's row.

Every window/threshold/fraction below is either a number this project already established
elsewhere, or an explicitly labeled judgment call -- never a guessed constant with no stated
reasoning:

  rolling_vdot          42-day trailing MAXIMUM (not an average/EWMA) of every running
                        activity's own VDOT (`perseverer.performance.vdot`, already computed by
                        `performance.py::refresh_vdot`) -- a maximum, not an average, because an
                        easy/recovery run's VDOT reads low from intensity, not fitness (the exact
                        reasoning `frontend/src/runningStats.ts::bestVdot` already documents for
                        its own "best of the period" convention -- see
                        `docs/DATA_DICTIONARY.md`'s own VDOT section). 42 days reuses this app's
                        own established fitness-lookback constant (`fitness.py`'s CTL time
                        constant) for consistency, but is a genuinely different mechanism (a hard
                        trailing-window max, not an exponentially-weighted average) -- never
                        confuse the two.

  max_hr_bpm            365-day trailing maximum of max heart rate across EVERY sport (not just
                        running -- a max-HR effort from cycling/hiit/a hard hike is equally
                        valid, and restricting to running would silently discard the real
                        maximum). A window this long is needed because a genuine near-max effort
                        doesn't happen every week; a shorter window would make this value flicker
                        based on incidental recent effort rather than physiology. Peer-reviewed
                        research is consistent that an individual's own empirically observed max
                        beats any age-based formula as the PRIMARY source (220-age carries
                        ~10-15bpm error even in its best documented forms) -- so this stays
                        empirical-first, never overridden by a formula once real data exists. A
                        brand-new athlete has no empirical max HR at all for weeks, though, so
                        when the empirical window is empty AND the athlete has a configured
                        `athlete.birthdate`, `max_hr_bpm` instead falls back to the Tanaka formula
                        (`208 - 0.7*age`, chosen over the older/cruder 220-age for its lower
                        documented error) for just that gap -- `max_hr_source` ("empirical" |
                        "formula_fallback" | null) records which path fired, same idea as
                        `threshold_hr_source` below. The moment one real max-HR observation
                        exists, the empirical value takes back over for that day forward.

  threshold_pace_s_per_km / aerobic_threshold_pace_s_per_km / predicted_5k_s / predicted_10k_s /
  predicted_half_marathon_s / predicted_marathon_s
                        Derived from `rolling_vdot` via `vdot.py`'s own
                        `compute_threshold_pace_s_per_km`/`predict_race_time_s` -- see that
                        module's docstring for the math and its literature basis.
                        `threshold_pace_s_per_km` is the anaerobic/lactate threshold (kept as the
                        original, unqualified field name -- every existing consumer, `hr_zones.py`
                        and `running_load.py` included, already means this one);
                        `aerobic_threshold_pace_s_per_km` is the newer aerobic threshold, always
                        slower, both from the same `rolling_vdot` via the same function at a
                        different fraction (`vdot.AEROBIC_THRESHOLD_VO2MAX_FRACTION`).

  threshold_hr_bpm / aerobic_threshold_hr_bpm
                        Empirical median heart rate among running activities in the same 365-day
                        window whose own grade-adjusted pace
                        (`perseverer.performance.avg_gap_speed_mps`, `gap.py`) falls within
                        `THRESHOLD_PACE_TOLERANCE` of that day's own threshold pace -- median, not
                        mean, to resist a single outlier (a cold-start HR spike, a chest-strap
                        dropout). Requires at least `MIN_THRESHOLD_HR_SAMPLES` qualifying runs;
                        below that, falls back to a fraction of that day's `max_hr_bpm`
                        (`THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR` for the anaerobic threshold,
                        `AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR` for the aerobic one --
                        see each constant's own docstring for its literature basis).
                        `threshold_hr_source`/`aerobic_threshold_hr_source` record which path
                        fired ("empirical" | "fallback" | null when neither is possible yet).
                        `compute_threshold_hr` (below) is the one function both branches share, so
                        the two thresholds can never silently drift onto different logic.

`ROLLING_VDOT_WINDOW_DAYS`, `MAX_HR_WINDOW_DAYS`, `THRESHOLD_HR_WINDOW_DAYS`,
`THRESHOLD_PACE_TOLERANCE`, `MIN_THRESHOLD_HR_SAMPLES`, `MAX_HR_METRIC_KEYS`,
`AVG_HR_METRIC_KEYS`, `priority_merge`, and `median` are exported (no leading underscore)
specifically so `threshold_analysis.py`'s own request-time factor-analysis query reuses the exact
same window/tolerance/merge logic this rollup does, rather than a second copy that could drift --
same reuse `vo2max_analysis.py` already established for `ROLLING_VDOT_WINDOW_DAYS`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection, delete, select

from perseverer.athlete_age import age_years_as_of
from perseverer.db.schema import activity, activity_metric, athlete, performance_daily_rollup
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.vdot import (
    AEROBIC_THRESHOLD_VO2MAX_FRACTION,
    RACE_DISTANCES_M,
    compute_threshold_pace_s_per_km,
    predict_race_time_s,
)

ROLLING_VDOT_WINDOW_DAYS = 42
MAX_HR_WINDOW_DAYS = 365
THRESHOLD_HR_WINDOW_DAYS = 365
# How close (as a fraction of threshold pace) a run's own pace must be to count as a real
# threshold-intensity effort -- wide enough for natural pace variance in a tempo/threshold
# session, narrow enough to exclude interval work (much faster) and easy runs (much slower). Used
# for both the anaerobic and aerobic threshold windows -- no literature basis found for treating
# them differently, and aerobic-threshold-paced (easy/steady) runs are if anything more common in
# real training data than anaerobic-threshold ones, so reusing the same tolerance risks no
# fewer qualifying samples.
THRESHOLD_PACE_TOLERANCE = 0.05
MIN_THRESHOLD_HR_SAMPLES = 3
# A representative point within the commonly-cited 85-92%-of-max-HR range for well-trained
# runners' lactate (anaerobic) threshold.
THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR = 0.88
# The aerobic threshold's own fallback fraction -- new, not previously computed. Esteve-Lanao,
# Sellés-Pérez, Arévalo-Chico & Cejuela (2026, Sports 14(1):29) measured heart rate at the first
# ventilatory threshold (VT1, aerobic threshold) at 85.1 ± 4.6% of HRpeak across 1,411 endurance-
# trained runners -- the same study `vdot.AEROBIC_THRESHOLD_VO2MAX_FRACTION` cites for its own
# %VO2max figure, kept consistent with that pace-domain choice rather than sourced separately.
AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR = 0.851
# Tanaka, Monahan & Seals (2001) -- max_hr_bpm's own formula fallback, used only when the 365-day
# empirical window (below) is empty. See this module's docstring for why empirical stays primary.
_TANAKA_MAX_HR_INTERCEPT = 208.0
_TANAKA_MAX_HR_AGE_COEFFICIENT = 0.7

# Same duplicated-tuple precedent already used four times in this codebase (activity_merge.py,
# activity_trim.py, insights/engine.py, api/routers/activities.py) -- add a fifth here rather
# than importing a router module into a plain computation module.
MAX_HR_METRIC_KEYS = ("fit.session.max_heart_rate", "strava.session.max_heart_rate")
AVG_HR_METRIC_KEYS = ("fit.session.avg_heart_rate", "strava.session.avg_heart_rate")


def priority_merge(rows: list[tuple[str, str, float]], keys: tuple[str, ...]) -> dict[str, float]:
    """`rows` is (activity_id, metric_key, value_num); keeps, per activity_id, the value whose
    metric_key is earliest in `keys` -- same alias-merge idiom as
    api/routers/activities.py::_aliased_metric_subquery, done in Python since these rows are
    already bulk-fetched rather than issuing a second per-activity query."""
    by_activity: dict[str, dict[str, float]] = {}
    for activity_id, metric_key, value_num in rows:
        by_activity.setdefault(activity_id, {})[metric_key] = value_num
    result: dict[str, float] = {}
    for activity_id, values in by_activity.items():
        for key in keys:
            if key in values:
                result[activity_id] = values[key]
                break
    return result


def median(values: list[float]) -> float:
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 == 1 else (values[mid - 1] + values[mid]) / 2


def compute_threshold_hr(
    window: list[tuple[str, float, float]],
    reference_pace_s_per_km: float | None,
    max_hr_bpm: float | None,
    fallback_fraction: float,
) -> tuple[float | None, str | None]:
    """`window` is (local_date, pace_s_per_km, avg_hr_bpm) for every VDOT-eligible run with a
    usable GAP pace and average HR in the relevant trailing window -- the same shape
    `refresh_performance_rollup`'s own `threshold_candidates` already builds, and what
    `threshold_analysis.py` rebuilds with full activity details for its own factor breakdown.
    One function for both the anaerobic and aerobic thresholds (called with a different
    `reference_pace_s_per_km`/`fallback_fraction` each time) so the two can never silently drift
    onto different empirical/fallback logic.
    """
    if reference_pace_s_per_km is None:
        return None, None
    lo = reference_pace_s_per_km * (1 - THRESHOLD_PACE_TOLERANCE)
    hi = reference_pace_s_per_km * (1 + THRESHOLD_PACE_TOLERANCE)
    qualifying_hrs = sorted(h for _, p, h in window if lo <= p <= hi)
    if len(qualifying_hrs) >= MIN_THRESHOLD_HR_SAMPLES:
        return median(qualifying_hrs), "empirical"
    if max_hr_bpm is not None:
        return fallback_fraction * max_hr_bpm, "fallback"
    return None, None


def refresh_performance_rollup(conn: Connection, *, athlete_id: str) -> None:
    """Recomputes the athlete's entire `performance_daily_rollup` history from scratch -- delete
    then reinsert, same idempotent-recompute model `refresh_fitness_rollup` uses. Never calls
    `conn.commit()` -- caller controls the transaction. A no-op (deletes existing rows, inserts
    nothing) if the athlete has no qualifying activity at all.
    """
    now = datetime.now(UTC)

    birthdate_str = conn.execute(
        select(athlete.c.birthdate).where(athlete.c.id == athlete_id)
    ).scalar_one_or_none()
    birthdate = date.fromisoformat(birthdate_str) if birthdate_str else None

    vdot_rows = conn.execute(
        select(
            activity_metric.c.activity_id, activity.c.local_date, activity_metric.c.value_num
        )
        .select_from(
            activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id)
        )
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == VDOT_METRIC_KEY,
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()

    avg_gap_by_activity: dict[str, float] = {
        r.activity_id: r.value_num
        for r in conn.execute(
            select(activity_metric.c.activity_id, activity_metric.c.value_num).where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.metric_key == AVG_GAP_METRIC_KEY,
            )
        )
    }

    avg_hr_raw = conn.execute(
        select(
            activity_metric.c.activity_id,
            activity_metric.c.metric_key,
            activity_metric.c.value_num,
        )
        .select_from(
            activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id)
        )
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key.in_(AVG_HR_METRIC_KEYS),
            activity.c.sport == "running",
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()
    avg_hr_by_activity = priority_merge(
        [(r.activity_id, r.metric_key, r.value_num) for r in avg_hr_raw], AVG_HR_METRIC_KEYS
    )

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
        )
    ).fetchall()
    max_hr_by_activity = priority_merge(
        [(r.activity_id, r.metric_key, r.value_num) for r in max_hr_raw], MAX_HR_METRIC_KEYS
    )
    max_hr_local_date_by_activity = {r.activity_id: r.local_date for r in max_hr_raw}

    conn.execute(
        delete(performance_daily_rollup).where(
            performance_daily_rollup.c.athlete_id == athlete_id
        )
    )

    if not vdot_rows and not max_hr_by_activity:
        return

    vdot_by_date: list[tuple[str, float]] = [(r.local_date, r.value_num) for r in vdot_rows]
    max_hr_by_date: list[tuple[str, float]] = [
        (max_hr_local_date_by_activity[aid], v) for aid, v in max_hr_by_activity.items()
    ]

    # One entry per VDOT-eligible run carrying everything threshold-HR needs to evaluate it:
    # (local_date, pace_s_per_km, avg_hr_bpm) -- only runs with a usable GAP speed *and* an avg
    # HR are candidates at all.
    threshold_candidates: list[tuple[str, float, float]] = []
    for r in vdot_rows:
        gap_speed = avg_gap_by_activity.get(r.activity_id)
        avg_hr = avg_hr_by_activity.get(r.activity_id)
        if gap_speed is not None and gap_speed > 0 and avg_hr is not None:
            threshold_candidates.append((r.local_date, 1000.0 / gap_speed, avg_hr))

    all_dates = [d for d, _ in vdot_by_date] + [d for d, _ in max_hr_by_date]
    if not all_dates:
        return
    start = date.fromisoformat(min(all_dates))
    end = now.date()

    # Sort once; the forward loop below advances a pointer into each sorted list rather than
    # re-scanning the whole list every day (each window is then just a bounded re-filter of
    # already-admitted entries -- O(days * window size), cheap even at ~1,400+ days, same cost
    # class fitness.py's own full-history recompute already accepts).
    vdot_by_date.sort(key=lambda t: t[0])
    max_hr_by_date.sort(key=lambda t: t[0])
    threshold_candidates.sort(key=lambda t: t[0])

    vdot_window: list[tuple[str, float]] = []
    max_hr_window: list[tuple[str, float]] = []
    threshold_window: list[tuple[str, float, float]] = []
    vdot_i = max_hr_i = threshold_i = 0

    rollup_rows = []
    day = start
    while day <= end:
        iso = day.isoformat()

        while vdot_i < len(vdot_by_date) and vdot_by_date[vdot_i][0] <= iso:
            vdot_window.append(vdot_by_date[vdot_i])
            vdot_i += 1
        vdot_cutoff = (day - timedelta(days=ROLLING_VDOT_WINDOW_DAYS - 1)).isoformat()
        vdot_window = [(d, v) for d, v in vdot_window if d >= vdot_cutoff]
        rolling_vdot = max((v for _, v in vdot_window), default=None)

        while max_hr_i < len(max_hr_by_date) and max_hr_by_date[max_hr_i][0] <= iso:
            max_hr_window.append(max_hr_by_date[max_hr_i])
            max_hr_i += 1
        max_hr_cutoff = (day - timedelta(days=MAX_HR_WINDOW_DAYS - 1)).isoformat()
        max_hr_window = [(d, v) for d, v in max_hr_window if d >= max_hr_cutoff]
        max_hr_bpm = max((v for _, v in max_hr_window), default=None)
        if max_hr_bpm is not None:
            max_hr_source: str | None = "empirical"
        elif birthdate is not None:
            age = age_years_as_of(birthdate, day)
            max_hr_bpm = _TANAKA_MAX_HR_INTERCEPT - _TANAKA_MAX_HR_AGE_COEFFICIENT * age
            max_hr_source = "formula_fallback"
        else:
            max_hr_source = None

        while (
            threshold_i < len(threshold_candidates)
            and threshold_candidates[threshold_i][0] <= iso
        ):
            threshold_window.append(threshold_candidates[threshold_i])
            threshold_i += 1
        threshold_cutoff = (day - timedelta(days=THRESHOLD_HR_WINDOW_DAYS - 1)).isoformat()
        threshold_window = [
            (d, p, h) for d, p, h in threshold_window if d >= threshold_cutoff
        ]

        threshold_pace_s_per_km = compute_threshold_pace_s_per_km(rolling_vdot)
        aerobic_threshold_pace_s_per_km = compute_threshold_pace_s_per_km(
            rolling_vdot, fraction=AEROBIC_THRESHOLD_VO2MAX_FRACTION
        )

        threshold_hr_bpm, threshold_hr_source = compute_threshold_hr(
            threshold_window,
            threshold_pace_s_per_km,
            max_hr_bpm,
            THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR,
        )
        aerobic_threshold_hr_bpm, aerobic_threshold_hr_source = compute_threshold_hr(
            threshold_window,
            aerobic_threshold_pace_s_per_km,
            max_hr_bpm,
            AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR,
        )

        predicted = {
            label: (
                predict_race_time_s(distance_m, rolling_vdot)
                if rolling_vdot is not None
                else None
            )
            for label, distance_m in RACE_DISTANCES_M.items()
        }

        rollup_rows.append(
            {
                "athlete_id": athlete_id,
                "local_date": iso,
                "rolling_vdot": rolling_vdot,
                "max_hr_bpm": max_hr_bpm,
                "max_hr_source": max_hr_source,
                "threshold_pace_s_per_km": threshold_pace_s_per_km,
                "threshold_hr_bpm": threshold_hr_bpm,
                "threshold_hr_source": threshold_hr_source,
                "aerobic_threshold_pace_s_per_km": aerobic_threshold_pace_s_per_km,
                "aerobic_threshold_hr_bpm": aerobic_threshold_hr_bpm,
                "aerobic_threshold_hr_source": aerobic_threshold_hr_source,
                "predicted_5k_s": predicted["5k"],
                "predicted_10k_s": predicted["10k"],
                "predicted_half_marathon_s": predicted["half_marathon"],
                "predicted_marathon_s": predicted["marathon"],
                "refreshed_at": now,
            }
        )
        day += timedelta(days=1)

    conn.execute(performance_daily_rollup.insert(), rollup_rows)
