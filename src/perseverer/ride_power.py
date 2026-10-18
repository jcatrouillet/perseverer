"""Cycling power fitness: FTP, FTP/kg, Critical Power (with W') and a VO2max estimate, all from the
rides' own power streams. The cycling counterparts of VDOT and the running VO2max, computed by
Perseverer itself -- never Garmin's precomputed figures, which stay archived but are not used here.

**Per ride, at import** (`refresh_ride_power`, incremental like `run_pacing.py`): the best average
power over 5 s, 30 s, 1, 3, 5, 12, 20, 30 and 60 minutes (`BEST_POWER_DURATIONS_S`, shown on the
ride's own page; 3-20 minutes also feed FTP and Critical Power), found with the performance
curve's own sliding window (`performance_curve.best_window_over_stream`, mean mode, the same gap
rules), stored as `activity_metric` rows `perseverer.power.best_<seconds>s_w`; and the ride's
max speed as its fastest 5-second average (`MAX_SPEED_KEY`, rate mode over the distance stream),
for the many rides whose file records no max speed of its own -- a 5-second window keeps one
GPS spike from posing as a top speed. A SIGNATURE_KEY row records
the inputs (algorithm version, stream file, trim window) so a ride is only read again when one of
them changes; rides without a power channel keep a signature too.

**Over time, at request time** (`compute_cycling_power`, a bounded lookup over those per-ride rows):
for a date, the best of each duration across the rides of the trailing `WINDOW_DAYS` (90) days.

- **FTP** = 95% of the best 20-minute power (Allen & Coggan's field test).
- **FTP/kg** = FTP / body weight on that date: the scale's latest reading, ignoring readings more
  than 15% from the median of all of them (a smart scale shared with someone else records their
  weight too -- confirmed on real data), else the weight on the ride's own FIT profile.
- **Critical Power and W'** from the two-parameter model, work = W' + CP x t: a least-squares line
  through (t, best power x t) for the 3-20 minute bests (Monod & Scherrer; Jones et al. 2010 on
  using 3-20 min efforts). CP is the slope (watts), W' the intercept (joules). Needs at least two
  durations, and is withheld when the fit is non-physical (CP or W' not positive).
- **VO2max**, ml/kg/min = 10.8 x (best 5-minute power / kg) + 7, the ACSM leg-cycling equation
  applied to the window's best 5 minutes, taken as the power at VO2max. It reads true only when
  that best was close to all-out; a ride with no hard 5 minutes in the window reads low. Not
  derived from heart rate: tried and dropped, because heart rate on real rides (often a wrist
  sensor on an indoor bike) correlated too poorly with power to extrapolate to max HR -- per-ride
  estimates ranged from 19 to 57. Like lab tests, it usually reads a few points below a running
  VO2max.

Every figure is None when its inputs are missing (no power ride in the window, no weight): never
a fabricated number.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from sqlalchemy import Connection, select

from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_stream,
    activity_trim_override,
    health_observation,
)
from perseverer.merge.engine import sports_in_family
from perseverer.metrics.registry import get_or_register_metric
from perseverer.performance_curve import best_window_over_stream

RIDE_POWER_VERSION = 2
BEST_POWER_DURATIONS_S: tuple[int, ...] = (5, 30, 60, 180, 300, 720, 1200, 1800, 3600)
# The 3-20 minute bests behind Critical Power (shorter is anaerobic, longer is endurance).
CP_DURATIONS_S: tuple[int, ...] = (180, 300, 720, 1200)
MAX_SPEED_WINDOW_S = 5
WINDOW_DAYS = 90
FTP_FRACTION_OF_20_MIN = 0.95
# ACSM leg-cycling metabolic equation: VO2 (ml/kg/min) = 10.8 x W/kg + 7.
VO2_PER_WATT_PER_KG = 10.8
VO2_RESTING_AND_UNLOADED = 7.0

# A reading this far from the median of every reading is someone else's (a shared scale).
WEIGHT_OUTLIER_FRACTION = 0.15

RIDE_SPORTS: tuple[str, ...] = sports_in_family("ride")
SIGNATURE_KEY = "perseverer.power.signature"
MAX_SPEED_KEY = "perseverer.speed.max_5s_mps"
_SOURCE = "perseverer"
_WEIGHT_KEYS = ("eufy.scale.weight", "apple_health.body_mass")
_PROFILE_WEIGHT_KEY = "fit.user_profile.weight"


def best_power_key(duration_s: int) -> str:
    return f"perseverer.power.best_{duration_s}s_w"


ALL_KEYS: tuple[str, ...] = (
    *(best_power_key(d) for d in BEST_POWER_DURATIONS_S),
    MAX_SPEED_KEY,
    SIGNATURE_KEY,
)


def compute_best_powers(elapsed_s: np.ndarray, power_w: np.ndarray) -> dict[int, float | None]:
    """Pure: the best average power for each of BEST_POWER_DURATIONS_S, from one ride's samples."""
    ok = ~(np.isnan(elapsed_s) | np.isnan(power_w))
    t = elapsed_s[ok].tolist()
    p = power_w[ok].tolist()
    return {d: best_window_over_stream(t, p, d, mode="mean") for d in BEST_POWER_DURATIONS_S}


def _read_channel(
    path: Path, channel: str, window: tuple[float, float] | None
) -> tuple[np.ndarray, np.ndarray]:
    table = pq.read_table(path, columns=["timestamp_utc", channel])
    ts = table.column("timestamp_utc").cast("int64").to_numpy(zero_copy_only=False)
    elapsed = (ts - ts[0]).astype(float) / 1_000_000 if ts.size else np.array([], dtype=float)
    values = table.column(channel).to_numpy(zero_copy_only=False).astype(float)
    if window is not None:
        keep = (elapsed >= window[0]) & (elapsed <= window[1])
        elapsed, values = elapsed[keep], values[keep]
    return elapsed, values


def compute_max_speed(elapsed_s: np.ndarray, distance_m: np.ndarray) -> float | None:
    """Pure: the fastest MAX_SPEED_WINDOW_S-second average speed (m/s) over a distance stream."""
    ok = ~(np.isnan(elapsed_s) | np.isnan(distance_m))
    t, d = elapsed_s[ok], distance_m[ok]
    if t.size < 2:
        return None
    d = np.maximum.accumulate(d - d[0])  # GPS can dip; the window needs a monotonic distance
    return best_window_over_stream(t.tolist(), d.tolist(), MAX_SPEED_WINDOW_S, mode="rate")


def _register_metrics(conn: Connection) -> None:
    for d in BEST_POWER_DURATIONS_S:
        get_or_register_metric(
            conn,
            metric_key=best_power_key(d),
            source=_SOURCE,
            display_name=f"Best {d // 60}-minute power",
            unit_si="W",
            category="performance",
            value_type="numeric",
        )
    get_or_register_metric(
        conn,
        metric_key=MAX_SPEED_KEY,
        source=_SOURCE,
        display_name="Max speed (fastest 5 s)",
        unit_si="m/s",
        category="performance",
        value_type="numeric",
    )
    get_or_register_metric(
        conn,
        metric_key=SIGNATURE_KEY,
        source=_SOURCE,
        display_name="Ride power analysis inputs",
        unit_si=None,
        category="performance",
        value_type="text",
    )


def _delete(conn: Connection, athlete_id: str, activity_ids: Iterable[str]) -> None:
    ids = list(activity_ids)
    if not ids:
        return
    conn.execute(
        activity_metric.delete().where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id.in_(ids),
            activity_metric.c.metric_key.in_(ALL_KEYS),
        )
    )


def refresh_ride_power(conn: Connection, parquet_dir: Path, *, athlete_id: str) -> int:
    """Brings every ride's best-power rows up to date; returns the number of rides (re)computed.
    Reads and computes first, then writes, so the write transaction stays short."""
    rides = conn.execute(
        select(
            activity.c.id,
            activity.c.start_time_utc,
            activity_stream.c.parquet_path,
            activity_stream.c.channels,
        )
        .select_from(activity)
        .join(activity_stream, activity_stream.c.activity_id == activity.c.id)
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.sport.in_(RIDE_SPORTS),
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()
    trims = {
        row.activity_start_time_utc: (row.trim_start_s or 0.0, row.trim_end_s or float("inf"))
        for row in conn.execute(
            select(
                activity_trim_override.c.activity_start_time_utc,
                activity_trim_override.c.trim_start_s,
                activity_trim_override.c.trim_end_s,
            ).where(activity_trim_override.c.athlete_id == athlete_id)
        )
    }
    signatures = {
        row.activity_id: row.value_text
        for row in conn.execute(
            select(activity_metric.c.activity_id, activity_metric.c.value_text).where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.metric_key == SIGNATURE_KEY,
            )
        )
    }

    rows: list[dict[str, object]] = []
    changed: list[str] = []
    now = datetime.now(UTC).replace(tzinfo=None)
    for ride in rides:
        window = trims.get(ride.start_time_utc)
        signature = json.dumps(
            {
                "version": RIDE_POWER_VERSION,
                "stream": ride.parquet_path,
                "trim": list(window) if window else None,
            },
            sort_keys=True,
        )
        if signatures.get(ride.id) == signature:
            continue
        changed.append(ride.id)
        values: list[tuple[str, float | None, str | None, str | None]] = [
            (SIGNATURE_KEY, None, signature, None)
        ]
        path = parquet_dir / ride.parquet_path
        channels = json.loads(ride.channels)
        if "power" in channels and path.exists():
            elapsed, power = _read_channel(path, "power", window)
            for duration_s, watts in compute_best_powers(elapsed, power).items():
                if watts is not None and watts > 0:
                    values.append((best_power_key(duration_s), watts, None, "W"))
        if "distance_m" in channels and path.exists():
            max_speed = compute_max_speed(*_read_channel(path, "distance_m", window))
            if max_speed is not None and max_speed > 0:
                values.append((MAX_SPEED_KEY, max_speed, None, "m/s"))
        rows += [
            {
                "athlete_id": athlete_id,
                "activity_id": ride.id,
                "metric_key": key,
                "value_num": num,
                "value_text": text,
                "unit": unit,
                "source": _SOURCE,
                "created_at": now,
            }
            for key, num, text, unit in values
        ]

    ride_ids = {r.id for r in rides}
    stale = [i for i in signatures if i not in ride_ids]
    if not stale and not changed:
        return 0
    _register_metrics(conn)
    _delete(conn, athlete_id, [*stale, *changed])
    if rows:
        conn.execute(activity_metric.insert(), rows)
    return len(changed)


# --------------------------------------------------------------------------- request-time model


@dataclass(frozen=True)
class RideRef:
    activity_id: str
    local_date: str
    name: str | None


@dataclass(frozen=True)
class CyclingPowerPoint:
    """The figures as of `as_of`, from the rides of the WINDOW_DAYS before it (inclusive)."""

    as_of: str
    ftp_w: float | None
    ftp_w_per_kg: float | None
    cp_w: float | None
    w_prime_j: float | None
    vo2max: float | None
    weight_kg: float | None
    best_power_w: dict[int, float] = field(default_factory=dict)
    # Which ride set each duration's best in the window.
    best_power_ride: dict[int, RideRef] = field(default_factory=dict)


@dataclass(frozen=True)
class CyclingPower:
    current: CyclingPowerPoint | None
    # One point per date a power ride happened, oldest first: the figures as of that ride.
    history: list[CyclingPowerPoint]
    last_power_ride_date: str | None
    missing: list[str]


def fit_critical_power(best_power_w: dict[int, float]) -> tuple[float, float] | None:
    """(CP watts, W' joules) from the least-squares line work = W' + CP x t through the bests;
    None with fewer than three durations, none of 12 minutes or longer (3 and 5 minutes alone span
    too little to fix the line), or a non-physical fit."""
    points = sorted((float(t), p * t) for t, p in best_power_w.items())
    if len(points) < 3 or points[-1][0] < 720:
        return None
    ts = np.array([t for t, _ in points])
    work = np.array([w for _, w in points])
    cp, w_prime = np.polyfit(ts, work, 1)
    if cp <= 0 or w_prime <= 0:
        return None
    return float(cp), float(w_prime)


def vo2max_from_power(watts: float, weight_kg: float) -> float:
    return VO2_PER_WATT_PER_KG * watts / weight_kg + VO2_RESTING_AND_UNLOADED


def _point(
    as_of: date,
    bests: list[tuple[str, str, str | None, int, float]],
    weight_kg: float | None,
) -> CyclingPowerPoint:
    """`bests` holds every (activity_id, local_date, name, duration, watts) in the window."""
    best_power: dict[int, float] = {}
    best_ride: dict[int, RideRef] = {}
    for activity_id, local_date, name, duration_s, watts in bests:
        if watts > best_power.get(duration_s, 0.0):
            best_power[duration_s] = watts
            best_ride[duration_s] = RideRef(activity_id, local_date, name)
    ftp = best_power[1200] * FTP_FRACTION_OF_20_MIN if 1200 in best_power else None
    cp_fit = fit_critical_power({d: w for d, w in best_power.items() if d in CP_DURATIONS_S})
    vo2max = (
        vo2max_from_power(best_power[300], weight_kg) if 300 in best_power and weight_kg else None
    )
    return CyclingPowerPoint(
        as_of=as_of.isoformat(),
        ftp_w=ftp,
        ftp_w_per_kg=ftp / weight_kg if ftp is not None and weight_kg else None,
        cp_w=cp_fit[0] if cp_fit else None,
        w_prime_j=cp_fit[1] if cp_fit else None,
        vo2max=vo2max,
        weight_kg=weight_kg,
        best_power_w=best_power,
        best_power_ride=best_ride,
    )


def compute_cycling_power(
    conn: Connection, *, athlete_id: str, as_of: date, exclude_commutes: bool = False
) -> CyclingPower:
    """The current figures (trailing WINDOW_DAYS ending `as_of`) and their history: the same
    figures as of each date a power ride happened. Bounded: reads only the per-ride best-power
    rows and the weight readings, never a stream."""
    keys = [best_power_key(d) for d in BEST_POWER_DURATIONS_S]
    best_query = (
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.name,
            activity_metric.c.metric_key,
            activity_metric.c.value_num,
        )
        .select_from(activity_metric.join(activity, activity.c.id == activity_metric.c.activity_id))
        .where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key.in_(keys),
            activity.c.deleted_at.is_(None),
            activity.c.local_date.is_not(None),
            activity.c.local_date <= as_of.isoformat(),
        )
    )
    if exclude_commutes:
        best_query = best_query.where(activity.c.is_commute.is_(False))
    rows = conn.execute(best_query).fetchall()
    duration_by_key = {best_power_key(d): d for d in BEST_POWER_DURATIONS_S}
    bests = sorted(
        (r.id, r.local_date, r.name, duration_by_key[r.metric_key], float(r.value_num))
        for r in rows
        if r.value_num is not None
    )
    bests.sort(key=lambda b: b[1])

    scale_rows = conn.execute(
        select(health_observation.c.local_date, health_observation.c.value_num)
        .where(
            health_observation.c.athlete_id == athlete_id,
            health_observation.c.metric_key.in_(_WEIGHT_KEYS),
            health_observation.c.value_num.is_not(None),
            health_observation.c.local_date <= as_of.isoformat(),
        )
        .order_by(health_observation.c.local_date)
    ).fetchall()
    profile_weight = {
        r.activity_id: r.value_num
        for r in conn.execute(
            select(activity_metric.c.activity_id, activity_metric.c.value_num).where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.metric_key == _PROFILE_WEIGHT_KEY,
                activity_metric.c.activity_id.in_({b[0] for b in bests}),
            )
        )
        if r.value_num
    }

    # Readings far from the median of all of them are someone else's on a shared scale.
    scale: list[tuple[str, float]] = [(r.local_date, float(r.value_num)) for r in scale_rows]
    if scale:
        median = float(np.median([v for _, v in scale]))
        scale = [(d, v) for d, v in scale if abs(v - median) <= WEIGHT_OUTLIER_FRACTION * median]

    def weight_on(day: str, ride_ids: Iterable[str]) -> float | None:
        reading = None
        for local_date, value in scale:
            if local_date > day:
                break
            reading = value
        if reading is not None:
            return float(reading)
        for ride_id in ride_ids:
            if ride_id in profile_weight:
                return float(profile_weight[ride_id])
        return None

    def point_as_of(day: date) -> CyclingPowerPoint | None:
        start = (day - timedelta(days=WINDOW_DAYS - 1)).isoformat()
        in_window = [b for b in bests if start <= b[1] <= day.isoformat()]
        if not in_window:
            return None
        latest_first = [b[0] for b in sorted(in_window, key=lambda b: b[1], reverse=True)]
        return _point(day, in_window, weight_on(day.isoformat(), latest_first))

    ride_dates = sorted({b[1] for b in bests})
    history = [p for d in ride_dates if (p := point_as_of(date.fromisoformat(d))) is not None]
    current = point_as_of(as_of)
    last_ride = ride_dates[-1] if ride_dates else None

    missing: list[str] = []
    if not bests:
        missing.append(
            "No ride with power yet: FTP, Critical Power and VO2max need a power meter or a smart "
            "trainer."
        )
    elif current is None:
        missing.append(
            f"No ride with power in the last {WINDOW_DAYS} days (the last was {last_ride}), so the "
            "figures below are as of that ride."
        )
    latest = current or (history[-1] if history else None)
    if latest is not None:
        if latest.ftp_w is None:
            missing.append("FTP needs a ride with at least 20 minutes of continuous power.")
        if latest.cp_w is None:
            missing.append(
                "Critical Power needs best efforts at two or more of 3, 5, 12 and 20 minutes."
            )
        if latest.weight_kg is None:
            missing.append(
                "FTP/kg and VO2max need your weight (a scale reading or a profile weight)."
            )
    return CyclingPower(
        current=current, history=history, last_power_ride_date=last_ride, missing=missing
    )
