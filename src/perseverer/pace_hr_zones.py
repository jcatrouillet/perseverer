"""A complete pace + heart-rate training-zone table -- five zones (Recovery, Basic Endurance,
Aerobic Threshold, Lactate Threshold, VO2 Max), each a real pace range and heart-rate range plus a
plain-language "when/how to use it" -- replacing the old point-in-time "Threshold Analysis" tab
(two single numbers, no ranges, no supporting evidence) with something an athlete can actually
train off of directly.

Deliberately built on the athlete's ALL-TIME best running performance, not a rolling window:
`performance_daily_rollup.rolling_vdot` exists specifically to track *current* fitness day to day
(a 42-day trailing max, see `performance_rollup.py`'s own docstring) -- the wrong basis for a
stable reference table an athlete keeps using for months.

**`profile_vdot` prefers a marked race over any training run, and this matters in practice, not
just in theory**: `perseverer.performance.vdot` is computed for *every* running activity
(`performance.py::refresh_vdot`), with no minimum-duration floor, and the Daniels VDOT formula is
calibrated against genuine race performances -- a short, all-out training segment (a hard strides
set, a track rep with the recovery jog trimmed off the recorded "moving" time) can post a VDOT well
above anything the athlete could hold for a real race distance. Confirmed against this app's own
real history, not a hypothetical: the single highest VDOT across every activity ever recorded
(47.7) came from a 12.6-minute, ~3.1 km segment named "Santa Clara Other" -- not a race at all --
while the best VDOT among activities the athlete actually marked as races (`activity.is_race`,
`garmin_activity_summary.py`'s own eventTypeId heuristic plus the athlete's own
`PATCH /activities/{id}/race` correction) tops out at a materially lower, more physiologically
honest 44.5 (a real 10K). Using the unfiltered all-time max there would have made every zone
boundary run too fast. `_all_time_best_vdot` therefore tries `is_race == True` activities first
and only falls back to the unfiltered all-time best (the original behavior) when the athlete has
no marked race at all -- `profile_vdot_source` ("race" | "training_run" | None) records which path
fired, the same provenance instinct `profile_max_hr_source` already established, and a
`training_run`-sourced profile gets its own explicit caveat in `missing` pointing at the "Mark as a
race" action (`ActivityDetailPage.tsx`) that would fix it. This is scoped to VDOT/pace only --
`profile_max_hr_bpm` stays the single highest heart rate across every activity ever recorded, any
sport, unchanged: `performance_rollup.py`'s own "a max-HR effort from cycling/hiit is equally
real" reasoning already applies here too, and a genuine physiological ceiling doesn't need a race
context to be real the way a race-calibrated pace formula does. Both `profile_vdot`/
`profile_max_hr_bpm` report the one activity that actually set them, not a silently-averaged
number.

## The five zones, and why they sit where they do

Every boundary is stated as a fraction of VO2max (pace side) or of max heart rate (HR side), each
with its own stated reasoning -- never a guessed number with no citation, the same discipline
`vdot.py`'s own `THRESHOLD_VO2MAX_FRACTION` already establishes.

**The two hard, lab-measured boundaries** (shared with `vdot.py`, not a second set of numbers):

- Aerobic threshold ("LT1"/"VT1", the Zone 2/Zone 3 boundary) --
  `vdot.AEROBIC_THRESHOLD_VO2MAX_FRACTION` = 0.73 VO2max; `AEROBIC_THRESHOLD_HR_FRACTION` = 0.851
  of max HR. Fathi, Shahidi & Alhusaen Aga (2025, *Int J Exercise Science* 18(5):1381-1392, n=12
  trained runners, gas-exchange/visual-inspection method) measured VT1 at 73.2 +/- 4.1% VO2max;
  Esteve-Lanao, Selles-Perez, Arevalo-Chico & Cejuela (2026, *Sports* 14(1):29, n=1,411
  endurance-trained runners, treadmill gas-exchange protocol) measured VT1 at 67.5-73.4% VO2peak
  and its heart rate at 85.1 +/- 4.6% of HRpeak. 0.73/0.851 are exactly the representative points
  `vdot.py`/`performance_rollup.py` already settled on for this same physiological point -- reused
  here, never re-derived.
- Lactate/anaerobic threshold ("LT2"/"VT2", the Zone 4/Zone 5 boundary) --
  `vdot.THRESHOLD_VO2MAX_FRACTION` = 0.88 VO2max (same reuse); `LACTATE_THRESHOLD_HR_FRACTION` =
  0.935 of max HR. The pace side reuses the app's existing representative point; the HR side is a
  deliberate, first-time use of Esteve-Lanao et al.'s own directly-measured VT2 HR figure (93.5
  +/- 2.5% of HRpeak) rather than `performance_rollup.py`'s existing 0.88 anaerobic-threshold-HR
  *fallback* fraction -- that existing constant is explicitly documented (see its own docstring)
  as "an older, more generic citation" kept unchanged there only because revising an
  already-relied-upon fallback used by existing athletes is a separate decision from building a
  brand-new feature. This feature has no such installed base to preserve, so it uses the more
  precise, modern, same-study figure outright.

**The two remaining boundaries are coaching judgment calls, stated plainly as such** -- no single
study prescribes them, since the two thresholds above are the only points sport science actually
measures directly:

- Zone 1/Zone 2 boundary (how far below aerobic threshold counts as "recovery" rather than
  "endurance"): `ZONE1_2_VO2MAX_FRACTION = 0.59` VO2max, `ZONE1_2_HR_FRACTION = 0.65` of max HR --
  the *floor*, not a fraction, of Jack Daniels' own published "Easy" (E) pace range (59-74%
  VO2max, ~65-78% HRmax, *Daniels' Running Formula*) -- the same Daniels-Gilbert model this
  entire app's VDOT is already built on (`vdot.py`'s own docstring), so this reuses an authority
  already load-bearing here rather than introducing a new one. Zone 2 (Basic Endurance, this
  boundary through the aerobic threshold at 0.73/0.851) is consequently almost exactly Daniels'
  own E pace range end to end (0.73 sits right at Daniels' own 0.74 E-pace ceiling) -- Zone 1
  (Recovery) is genuinely *below* Daniels' own easy-pace floor, not merely "a bit under threshold."
  An earlier version of this boundary used 90% of the Zone 2/3 value instead (a generic
  ventilatory-threshold-zone-calculator convention with no direct tie to this app's own model) --
  confirmed too narrow directly against the athlete's own real recovery runs, which routinely sit
  well below that boundary's own pace, and revised to Daniels' own number instead.
- Zone 3/Zone 4 boundary (splitting the whole aerobic-threshold-to-lactate-threshold range into
  its own "aerobic threshold" and "lactate threshold" halves): the exact midpoint between the two
  lab-measured thresholds, in the same units each side is already expressed in. This mirrors
  Seiler's own three-zone skeleton (Seiler & Kjerland, 2006, "Quantifying training intensity
  distribution in elite endurance athletes," *Scand J Med Sci Sports*; Seiler, 2010, "What is best
  practice for training intensity and duration distribution in endurance athletes?", *Int J Sports
  Physiol Perform*) -- below LT1, between LT1 and LT2, above LT2 -- subdivided once more on each
  end (Zone 1/2 splits "below LT1" into recovery vs. endurance; Zone 3/4 splits "between LT1 and
  LT2" into aerobic-threshold-side vs. lactate-threshold-side) to reach five zones instead of
  three, rather than inventing a materially different model.

## How each zone's actual range is computed -- empirical first, formula only as fallback

The PACE side of every boundary is a straight function of `profile_vdot` via `vdot.
compute_threshold_pace_s_per_km` (already parameterized by an arbitrary VO2max fraction -- the
same function `performance_rollup.py` calls twice for the two existing thresholds, called several
more times here for the extra boundaries).

The HR side is NOT simply "fraction times profile_max_hr_bpm" by default, even though that number
is always computed as the fallback -- consistent with `performance_rollup.py::
compute_threshold_hr`'s own empirical-first philosophy, extended from "the two threshold points"
to "every zone": for each zone, every one of the athlete's own qualifying running activities
(VDOT-eligible, with a real avg HR and a GAP pace that falls inside that zone's own pace band)
across their *entire* history contributes its own avg HR, and the zone's reported HR range is the
25th-75th percentile of that
real, empirical distribution -- not a formula, and not the lab-study fraction alone -- whenever
there's enough real data to trust (`MIN_ZONE_HR_SAMPLES`, reusing `performance_rollup.
MIN_THRESHOLD_HR_SAMPLES`'s own bar for the same reason). Below that sample count, the zone falls
back to the formula fraction, widened into a +/- `FALLBACK_HR_HALF_WIDTH_BPM` band around it since
a bare fallback point isn't a "range" on its own. Every zone's own qualifying runs are returned too
(evenly sampled down to `MAX_SAMPLE_RUNS_PER_ZONE` when there are more, by heart rate so the
low/middle/high of the real spread all stay represented) -- this is the literal answer to "why
these numbers," the same "list the driving/qualifying activities" instinct `vo2max_analysis.py`/
the old `threshold_analysis.py` already established for a single point, now extended to a whole
zone's worth of real running history.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Connection, select

from perseverer.athlete_age import age_years_as_of
from perseverer.db.schema import activity, activity_metric, athlete
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.performance_rollup import (
    AVG_HR_METRIC_KEYS,
    MAX_HR_METRIC_KEYS,
    MIN_THRESHOLD_HR_SAMPLES,
    priority_merge,
)
from perseverer.vdot import (
    AEROBIC_THRESHOLD_VO2MAX_FRACTION,
    THRESHOLD_VO2MAX_FRACTION,
    compute_threshold_pace_s_per_km,
)

# Tanaka, Monahan & Seals (2001) -- same formula/fallback-only-when-empirical-is-empty posture
# `performance_rollup.py` already uses for max_hr_bpm; duplicated rather than imported since that
# module's own copies are private (a leading underscore, deliberately not part of its public
# surface) -- the same "small duplicated constant across modules" precedent this codebase already
# accepts elsewhere (see `performance_rollup.py`'s own docstring on `MAX_HR_METRIC_KEYS`).
_TANAKA_MAX_HR_INTERCEPT = 208.0
_TANAKA_MAX_HR_AGE_COEFFICIENT = 0.7

AEROBIC_THRESHOLD_HR_FRACTION = 0.851
LACTATE_THRESHOLD_HR_FRACTION = 0.935

# Jack Daniels' own published "Easy" (E) pace range floor -- 59% VO2max, ~65% HRmax (*Daniels'
# Running Formula*) -- reused directly rather than derived as a fraction of the aerobic threshold;
# see the module docstring's "Zone 1/Zone 2 boundary" paragraph for why.
ZONE1_2_VO2MAX_FRACTION = 0.59
ZONE1_2_HR_FRACTION = 0.65

ZONE2_3_VO2MAX_FRACTION = AEROBIC_THRESHOLD_VO2MAX_FRACTION
ZONE3_4_VO2MAX_FRACTION = (AEROBIC_THRESHOLD_VO2MAX_FRACTION + THRESHOLD_VO2MAX_FRACTION) / 2
ZONE4_5_VO2MAX_FRACTION = THRESHOLD_VO2MAX_FRACTION

ZONE2_3_HR_FRACTION = AEROBIC_THRESHOLD_HR_FRACTION
ZONE3_4_HR_FRACTION = (AEROBIC_THRESHOLD_HR_FRACTION + LACTATE_THRESHOLD_HR_FRACTION) / 2
ZONE4_5_HR_FRACTION = LACTATE_THRESHOLD_HR_FRACTION

# (vo2max_lo, vo2max_hi) per zone, None = open-ended (zone 1's slow end, zone 5's fast end).
_ZONE_VO2MAX_BOUNDS: tuple[tuple[float | None, float | None], ...] = (
    (None, ZONE1_2_VO2MAX_FRACTION),
    (ZONE1_2_VO2MAX_FRACTION, ZONE2_3_VO2MAX_FRACTION),
    (ZONE2_3_VO2MAX_FRACTION, ZONE3_4_VO2MAX_FRACTION),
    (ZONE3_4_VO2MAX_FRACTION, ZONE4_5_VO2MAX_FRACTION),
    (ZONE4_5_VO2MAX_FRACTION, None),
)
# (hr_fraction_lo, hr_fraction_hi) per zone, same open-ended convention.
_ZONE_HR_FRACTION_BOUNDS: tuple[tuple[float | None, float | None], ...] = (
    (None, ZONE1_2_HR_FRACTION),
    (ZONE1_2_HR_FRACTION, ZONE2_3_HR_FRACTION),
    (ZONE2_3_HR_FRACTION, ZONE3_4_HR_FRACTION),
    (ZONE3_4_HR_FRACTION, ZONE4_5_HR_FRACTION),
    (ZONE4_5_HR_FRACTION, None),
)

ZONE_LABELS: tuple[str, ...] = (
    "Recovery",
    "Basic Endurance",
    "Aerobic Threshold",
    "Lactate Threshold",
    "VO2 Max",
)

ZONE_DESCRIPTIONS: tuple[str, ...] = (
    "Very easy effort for warm-ups, cool-downs, and active-recovery days -- should feel almost "
    "too easy, fully conversational the entire time. Use it the day after a hard session, or "
    "for the first/last few minutes of any run.",
    "The pace most easy and long runs should sit in -- builds aerobic capacity and mitochondrial "
    "density with minimal fatigue, sustainable for hours, still fully conversational. This "
    "should be the bulk of a week's total running time.",
    "Sustained \"steady state\" effort right around your aerobic threshold -- comfortably hard, "
    "breathing noticeably deeper but still controlled in short sentences. Good for extended "
    "tempo runs (30-60+ min) and marathon-pace-adjacent work.",
    "Sustained effort right around your lactate threshold -- the classic \"comfortably hard\" "
    "tempo/threshold-interval pace, typically sustainable for 20-60 minutes continuously or in "
    "long intervals (2 x 15-20 min). Trains lactate clearance and raises the pace you can hold "
    "before fatigue accumulates quickly.",
    "Hard interval effort at or above the pace/HR that drives VO2max -- short, high-intensity "
    "reps (2-6 minutes) with close-to-full recovery between. The primary stimulus for raising "
    "VO2max itself; not sustainable continuously.",
)

MIN_ZONE_HR_SAMPLES = MIN_THRESHOLD_HR_SAMPLES
MAX_SAMPLE_RUNS_PER_ZONE = 12
# A bare fallback formula point isn't a "range" on its own; +/- this many bpm gives the table a
# usable band without overclaiming precision the fallback path doesn't actually have.
FALLBACK_HR_HALF_WIDTH_BPM = 4.0


@dataclass
class ActivityRef:
    activity_id: str
    local_date: str
    name: str | None
    sport: str
    distance_m: float | None
    duration_s: float | None


@dataclass
class ZoneRunSample(ActivityRef):
    pace_s_per_km: float
    avg_hr_bpm: float


@dataclass
class PaceHrZone:
    number: int
    label: str
    description: str
    # Faster/slower edges of the pace band (seconds/km) -- "fast" < "slow" numerically, since a
    # smaller pace value is a faster pace. Either can be None at the two open ends (zone 1 has no
    # slow-side floor, zone 5 has no fast-side ceiling).
    pace_fast_s_per_km: float | None
    pace_slow_s_per_km: float | None
    hr_low_bpm: int | None
    hr_high_bpm: int | None
    hr_source: str | None  # "empirical" | "formula_fallback" | None
    qualifying_run_count: int
    sample_runs: list[ZoneRunSample] = field(default_factory=list)


@dataclass
class PaceHrZonesResult:
    as_of: str
    profile_vdot: float | None
    profile_vdot_activity: ActivityRef | None
    profile_vdot_source: str | None  # "race" | "training_run" | None
    profile_max_hr_bpm: float | None
    profile_max_hr_source: str | None  # "empirical" | "formula_fallback" | None
    zones: list[PaceHrZone]
    missing: list[str]


def _all_time_best_vdot(
    conn: Connection, *, athlete_id: str, as_of: date, races_only: bool
) -> tuple[float | None, ActivityRef | None]:
    where = [
        activity_metric.c.athlete_id == athlete_id,
        activity_metric.c.metric_key == VDOT_METRIC_KEY,
        activity.c.deleted_at.is_(None),
        activity.c.local_date <= as_of.isoformat(),
    ]
    if races_only:
        where.append(activity.c.is_race.is_(True))
    row = conn.execute(
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.name,
            activity.c.sport,
            activity.c.distance_m,
            activity.c.moving_duration_s,
            activity_metric.c.value_num,
        )
        .select_from(activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id))
        .where(*where)
        .order_by(activity_metric.c.value_num.desc())
        .limit(1)
    ).fetchone()
    if row is None:
        return None, None
    ref = ActivityRef(
        activity_id=row.id,
        local_date=row.local_date,
        name=row.name,
        sport=row.sport,
        distance_m=row.distance_m,
        duration_s=row.moving_duration_s,
    )
    return row.value_num, ref


def _all_time_max_hr(
    conn: Connection, *, athlete_id: str, as_of: date
) -> tuple[float | None, str | None]:
    rows = conn.execute(
        select(
            activity_metric.c.activity_id, activity_metric.c.metric_key, activity_metric.c.value_num
        )
        .select_from(activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id))
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key.in_(MAX_HR_METRIC_KEYS),
            activity.c.deleted_at.is_(None),
            activity.c.local_date <= as_of.isoformat(),
        )
    ).fetchall()
    merged = priority_merge(
        [(r.activity_id, r.metric_key, r.value_num) for r in rows], MAX_HR_METRIC_KEYS
    )
    if merged:
        return max(merged.values()), "empirical"

    birthdate_str = conn.execute(
        select(athlete.c.birthdate).where(athlete.c.id == athlete_id)
    ).scalar_one_or_none()
    if birthdate_str is not None:
        age = age_years_as_of(date.fromisoformat(birthdate_str), as_of)
        return _TANAKA_MAX_HR_INTERCEPT - _TANAKA_MAX_HR_AGE_COEFFICIENT * age, "formula_fallback"
    return None, None


def _all_qualifying_runs(
    conn: Connection, *, athlete_id: str, as_of: date
) -> list[tuple[str, str, str | None, str, float | None, float | None, float, float]]:
    """Every VDOT-eligible running activity, ever, with a usable GAP pace and avg HR -- the same
    per-row shape `performance_rollup.py::refresh_performance_rollup` builds for its own
    `threshold_candidates`, just unbounded by any trailing window (this feature is a stable,
    all-time profile, not a day-to-day rollup) and carrying full activity details for the
    "why these numbers" sample list. Returns
    (activity_id, local_date, name, sport, distance_m, duration_s, pace_s_per_km, avg_hr_bpm).
    """
    vdot_rows = conn.execute(
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.name,
            activity.c.sport,
            activity.c.distance_m,
            activity.c.moving_duration_s,
        )
        .select_from(activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id))
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == VDOT_METRIC_KEY,
            activity.c.deleted_at.is_(None),
            activity.c.local_date <= as_of.isoformat(),
        )
    ).fetchall()
    if not vdot_rows:
        return []
    activity_ids = [r.id for r in vdot_rows]

    avg_gap_by_activity: dict[str, float] = {
        r.activity_id: r.value_num
        for r in conn.execute(
            select(activity_metric.c.activity_id, activity_metric.c.value_num).where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.metric_key == AVG_GAP_METRIC_KEY,
                activity_metric.c.activity_id.in_(activity_ids),
            )
        )
    }
    avg_hr_raw = conn.execute(
        select(
            activity_metric.c.activity_id, activity_metric.c.metric_key, activity_metric.c.value_num
        ).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key.in_(AVG_HR_METRIC_KEYS),
            activity_metric.c.activity_id.in_(activity_ids),
        )
    ).fetchall()
    avg_hr_by_activity = priority_merge(
        [(r.activity_id, r.metric_key, r.value_num) for r in avg_hr_raw], AVG_HR_METRIC_KEYS
    )

    out = []
    for r in vdot_rows:
        gap_speed = avg_gap_by_activity.get(r.id)
        avg_hr = avg_hr_by_activity.get(r.id)
        if gap_speed is None or gap_speed <= 0 or avg_hr is None:
            continue
        out.append(
            (
                r.id,
                r.local_date,
                r.name,
                r.sport,
                r.distance_m,
                r.moving_duration_s,
                1000.0 / gap_speed,
                avg_hr,
            )
        )
    return out


def _sample_evenly(runs: list[ZoneRunSample], cap: int) -> list[ZoneRunSample]:
    """`runs` sorted by avg_hr_bpm ascending; returns all of them if there are `cap` or fewer,
    otherwise an evenly-spaced subset (always including the lowest and highest) so the sample
    still shows the low/middle/high of the real observed spread rather than an arbitrary slice."""
    n = len(runs)
    if n <= cap:
        return runs
    if cap <= 1:
        return runs[:1]
    indices = {round(i * (n - 1) / (cap - 1)) for i in range(cap)}
    return [runs[i] for i in sorted(indices)]


def compute_pace_hr_zones(conn: Connection, *, athlete_id: str, as_of: date) -> PaceHrZonesResult:
    profile_vdot, vdot_activity = _all_time_best_vdot(
        conn, athlete_id=athlete_id, as_of=as_of, races_only=True
    )
    profile_vdot_source: str | None = "race" if profile_vdot is not None else None
    if profile_vdot is None:
        profile_vdot, vdot_activity = _all_time_best_vdot(
            conn, athlete_id=athlete_id, as_of=as_of, races_only=False
        )
        if profile_vdot is not None:
            profile_vdot_source = "training_run"
    profile_max_hr_bpm, max_hr_source = _all_time_max_hr(conn, athlete_id=athlete_id, as_of=as_of)

    missing: list[str] = []
    if profile_vdot is None:
        missing.append(
            "No qualifying run yet -- pace zones need at least one run lasting roughly 11+ "
            "minutes with distance and pace data; shorter efforts don't fit the aerobic model "
            "this is built on."
        )
    elif profile_vdot_source == "training_run":
        missing.append(
            "No activity in your history is marked as a race, so this profile is based on your "
            "single best training effort instead -- VDOT is calibrated against real race "
            "performances, so a short, all-out training segment can read as fitter than a race "
            "would actually show, making every zone below run faster than it should. Use "
            '"Mark as a race" on a past race\'s activity page to fix this.'
        )
    if profile_max_hr_bpm is None:
        missing.append(
            "No heart rate data recorded yet, and no birthdate set in Settings to fall back to "
            "an age-based estimate -- HR ranges can't be computed without one or the other."
        )

    qualifying = _all_qualifying_runs(conn, athlete_id=athlete_id, as_of=as_of)

    zones: list[PaceHrZone] = []
    for i in range(5):
        vo2max_lo, vo2max_hi = _ZONE_VO2MAX_BOUNDS[i]
        # A higher %VO2max is a faster pace (lower seconds/km) -- the zone's slow edge comes from
        # its own lower %VO2max bound, the fast edge from its higher bound.
        pace_slow = (
            compute_threshold_pace_s_per_km(profile_vdot, vo2max_lo)
            if vo2max_lo is not None
            else None
        )
        pace_fast = (
            compute_threshold_pace_s_per_km(profile_vdot, vo2max_hi)
            if vo2max_hi is not None
            else None
        )

        hr_fraction_lo, hr_fraction_hi = _ZONE_HR_FRACTION_BOUNDS[i]
        formula_hr_low = (
            hr_fraction_lo * profile_max_hr_bpm
            if hr_fraction_lo is not None and profile_max_hr_bpm is not None
            else None
        )
        formula_hr_high = (
            hr_fraction_hi * profile_max_hr_bpm
            if hr_fraction_hi is not None and profile_max_hr_bpm is not None
            else None
        )

        zone_runs = [
            ZoneRunSample(
                activity_id=aid,
                local_date=local_date,
                name=name,
                sport=sport,
                distance_m=distance_m,
                duration_s=duration_s,
                pace_s_per_km=pace,
                avg_hr_bpm=hr,
            )
            for aid, local_date, name, sport, distance_m, duration_s, pace, hr in qualifying
            if (pace_fast is None or pace >= pace_fast) and (pace_slow is None or pace <= pace_slow)
        ]
        zone_runs.sort(key=lambda r: r.avg_hr_bpm)

        if len(zone_runs) >= MIN_ZONE_HR_SAMPLES:
            hrs = [r.avg_hr_bpm for r in zone_runs]
            q1, _, q3 = statistics.quantiles(hrs, n=4, method="inclusive")
            hr_low, hr_high, hr_source = q1, q3, "empirical"
        elif formula_hr_low is not None or formula_hr_high is not None:
            center = formula_hr_high if formula_hr_high is not None else formula_hr_low
            assert center is not None
            hr_low = (
                formula_hr_low
                if formula_hr_low is not None
                else center - FALLBACK_HR_HALF_WIDTH_BPM
            )
            hr_high = (
                formula_hr_high
                if formula_hr_high is not None
                else center + FALLBACK_HR_HALF_WIDTH_BPM
            )
            hr_source = "formula_fallback"
        else:
            hr_low, hr_high, hr_source = None, None, None

        zones.append(
            PaceHrZone(
                number=i + 1,
                label=ZONE_LABELS[i],
                description=ZONE_DESCRIPTIONS[i],
                pace_fast_s_per_km=pace_fast,
                pace_slow_s_per_km=pace_slow,
                hr_low_bpm=round(hr_low) if hr_low is not None else None,
                hr_high_bpm=round(hr_high) if hr_high is not None else None,
                hr_source=hr_source,
                qualifying_run_count=len(zone_runs),
                sample_runs=_sample_evenly(zone_runs, MAX_SAMPLE_RUNS_PER_ZONE),
            )
        )

    return PaceHrZonesResult(
        as_of=as_of.isoformat(),
        profile_vdot=profile_vdot,
        profile_vdot_activity=vdot_activity,
        profile_vdot_source=profile_vdot_source,
        profile_max_hr_bpm=profile_max_hr_bpm,
        profile_max_hr_source=max_hr_source,
        zones=zones,
        missing=missing,
    )
