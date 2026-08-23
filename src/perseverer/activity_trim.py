"""The athlete's own correction for a recording that includes a stretch of car travel it never
should have -- see `transport_mix.py` for the detection heuristic that surfaces the "want to trim
this?" prompt in the first place.

Same durable-override shape as `bouldering_overrides.py`/`sport_override.py`, for the same
reason: `sync rebuild` wipes and re-derives `activity`/`lap`/`route_geom` from raw bytes on every
run, so a bare `UPDATE activity SET distance_m = ...` would silently vanish on the next rebuild.
`activity_trim_override` is the durable record instead (never wiped -- see rebuild.py's
`_REBUILDABLE_TABLES`), and `apply_activity_trim_overrides` re-applies it after every rebuild.

**What a trim can and can't honestly recompute.** Distance, duration, elevation gain, and avg/max
heart rate are all directly re-derivable from the kept window of the activity's own per-second
Parquet stream -- the same fields the device itself would have reported for a recording that
never included the car segment. Calories and training load are not: both are proprietary
Firstbeat computations over the *original, full* recording, with no way to honestly reconstruct
"calories for just the kept portion" from raw stream data. Rather than presenting a fabricated
proportional estimate as a real number, `set_activity_trim` clears both to unknown -- exactly
`activity.calories = NULL` and the `fit.session.training_load_peak` `activity_metric` row
deleted, so a trimmed activity reads as "these were never known for this recording," not as an
invented number the athlete could later mistake for something the device measured.

**Undo is not "recompute with no trim window"** -- that would still lose calories/training load
forever, since `_recompute_window` never had them to begin with. `clear_activity_trim` instead
finds this activity's primary source's own archived raw bytes (via `activity_source_link` /
`raw_object`) and reparses them with `reparse.py::reparse_raw_object` -- the same "raw bytes back
into a parsed activity, no re-archiving, no merge-matching" helper the sources-split endpoint
already relies on -- restoring every field, including the two a trim itself can never recover,
with full original fidelity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import cast

import duckdb
import polyline as polyline_codec
from sqlalchemy import Connection, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from perseverer.archive import read_raw_bytes
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    activity_stream,
    activity_trim_override,
    lap,
    raw_object,
    route_geom,
)
from perseverer.fit.types import CanonicalActivity
from perseverer.reparse import reparse_raw_object

# Mirrors api/routers/activities.py's own AVG_HR_METRIC_KEYS/MAX_HR_METRIC_KEYS -- duplicated
# rather than imported to avoid a circular import (the router imports this module, not the
# reverse). A trimmed activity's avg/max heart rate is stale under *any* source's key, not just
# its primary one, so every matching row gets corrected, not just one.
_AVG_HR_METRIC_KEYS = ("fit.session.avg_heart_rate", "strava.session.avg_heart_rate")
_MAX_HR_METRIC_KEYS = ("fit.session.max_heart_rate", "strava.session.max_heart_rate")
# Same EAV-field shape as avg/max heart rate above (ActivityStatsGrid.tsx's own "Elevation loss"
# tile reads this, not activity.elevation_gain_m's descent counterpart -- there isn't one; total
# ascent is a plain activity column but total descent only ever lived here) -- caught by a real
# browser check against the flagged activity, which kept showing the full untrimmed descent
# after a trim until this was added.
_TOTAL_DESCENT_METRIC_KEYS = ("fit.session.total_descent", "strava.session.total_descent")
_TRAINING_LOAD_METRIC_KEY = "fit.session.training_load_peak"


@dataclass(frozen=True)
class _WindowSummary:
    distance_m: float | None
    duration_s: float | None
    elevation_gain_m: float | None
    elevation_loss_m: float | None
    avg_hr: float | None
    max_hr: float | None
    points: list[tuple[float, float]]  # (lat, lon), timestamp-ordered


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _recompute_window(
    con: duckdb.DuckDBPyConnection,
    parquet_path: Path,
    available_channels: frozenset[str],
    *,
    trim_start_s: float,
    trim_end_s: float,
) -> _WindowSummary:
    """One read of the activity's own Parquet stream, restricted to `[trim_start_s, trim_end_s]`
    elapsed seconds from its first recorded sample. `available_channels` (the activity's own
    `activity_stream.channels`, the same ingest-time-written, trusted list `stream_query.py`'s
    `downsample` validates client-requested channels against) is what lets this select only the
    channels this particular file actually has -- an indoor activity with no GPS, or a device
    that never recorded altitude, doesn't crash this, it just yields fewer fields."""
    wanted = ["lat", "lon", "distance_m", "altitude_m", "heart_rate"]
    select_list = ", ".join(
        _quote(c) if c in available_channels else f"NULL AS {_quote(c)}" for c in wanted
    )
    rows = con.execute(
        f"SELECT epoch(timestamp_utc) AS ts, {select_list} FROM read_parquet(?) "
        "ORDER BY timestamp_utc",
        [str(parquet_path)],
    ).fetchall()
    if not rows:
        return _WindowSummary(None, None, None, None, None, None, [])

    t0 = rows[0][0]
    windowed = [r for r in rows if trim_start_s <= (r[0] - t0) <= trim_end_s]
    if not windowed:
        return _WindowSummary(None, None, None, None, None, None, [])

    elapsed = [r[0] - t0 for r in windowed]
    duration_s = elapsed[-1] - elapsed[0]

    distances = [r[3] for r in windowed if r[3] is not None]
    distance_m = (max(distances) - min(distances)) if distances else None

    altitudes = [r[4] for r in windowed if r[4] is not None]
    elevation_gain_m = None
    elevation_loss_m = None
    if len(altitudes) >= 2:
        elevation_gain_m = sum(max(0.0, b - a) for a, b in pairwise(altitudes))
        elevation_loss_m = sum(max(0.0, a - b) for a, b in pairwise(altitudes))

    hrs = [r[5] for r in windowed if r[5] is not None]
    avg_hr = (sum(hrs) / len(hrs)) if hrs else None
    max_hr = max(hrs) if hrs else None

    points = [(r[1], r[2]) for r in windowed if r[1] is not None and r[2] is not None]

    return _WindowSummary(
        distance_m, duration_s, elevation_gain_m, elevation_loss_m, avg_hr, max_hr, points
    )


def _start_time_for(conn: Connection, *, athlete_id: str, activity_id: str) -> datetime:
    row = conn.execute(
        select(activity.c.start_time_utc).where(
            activity.c.id == activity_id, activity.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise ValueError(f"no activity {activity_id!r} for athlete {athlete_id!r}")
    return cast(datetime, row.start_time_utc)


def _stream_location(
    conn: Connection, *, activity_id: str
) -> tuple[Path, frozenset[str]] | None:
    row = conn.execute(
        select(activity_stream.c.parquet_path, activity_stream.c.channels).where(
            activity_stream.c.activity_id == activity_id
        )
    ).fetchone()
    if row is None:
        return None
    return Path(row.parquet_path), frozenset(json.loads(row.channels))


def _write_window_to_activity(
    conn: Connection, *, athlete_id: str, activity_id: str, window: _WindowSummary
) -> None:
    """Writes a recomputed window's fields onto the *existing* `activity`/`activity_metric`/
    `route_geom`/`lap` rows -- shared by `set_activity_trim` (the whole activity's own window)
    and its own per-lap clipping, and by `apply_activity_trim_overrides` after a rebuild."""
    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(
        activity.update()
        .where(activity.c.id == activity_id)
        .values(
            distance_m=window.distance_m,
            duration_s=window.duration_s,
            moving_duration_s=window.duration_s,
            elevation_gain_m=window.elevation_gain_m,
            calories=None,
            updated_at=now,
        )
    )
    conn.execute(
        delete(activity_metric).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.metric_key == _TRAINING_LOAD_METRIC_KEY,
        )
    )
    if window.avg_hr is not None:
        conn.execute(
            activity_metric.update()
            .where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.activity_id == activity_id,
                activity_metric.c.metric_key.in_(_AVG_HR_METRIC_KEYS),
            )
            .values(value_num=window.avg_hr)
        )
    if window.max_hr is not None:
        conn.execute(
            activity_metric.update()
            .where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.activity_id == activity_id,
                activity_metric.c.metric_key.in_(_MAX_HR_METRIC_KEYS),
            )
            .values(value_num=window.max_hr)
        )
    if window.elevation_loss_m is not None:
        conn.execute(
            activity_metric.update()
            .where(
                activity_metric.c.athlete_id == athlete_id,
                activity_metric.c.activity_id == activity_id,
                activity_metric.c.metric_key.in_(_TOTAL_DESCENT_METRIC_KEYS),
            )
            .values(value_num=window.elevation_loss_m)
        )

    if window.points:
        encoded = polyline_codec.encode(window.points)
        lats = [p[0] for p in window.points]
        lngs = [p[1] for p in window.points]
        conn.execute(
            route_geom.update()
            .where(route_geom.c.activity_id == activity_id)
            .values(
                encoded_polyline=encoded,
                simplified_polyline=encoded,
                min_lat=min(lats),
                min_lng=min(lngs),
                max_lat=max(lats),
                max_lng=max(lngs),
                start_lat=window.points[0][0],
                start_lng=window.points[0][1],
                end_lat=window.points[-1][0],
                end_lng=window.points[-1][1],
            )
        )


def set_activity_trim(
    conn: Connection,
    con: duckdb.DuckDBPyConnection,
    parquet_dir: Path,
    *,
    athlete_id: str,
    activity_id: str,
    trim_start_s: float | None,
    trim_end_s: float | None,
) -> None:
    """Records the trim (upserting on the athlete/start_time_utc identity, so re-adjusting an
    already-trimmed activity just replaces the previous trim) and applies it to the live
    `activity`/`activity_metric`/`route_geom`/`lap` rows immediately. Raises `ValueError` if the
    activity has no Parquet stream to recompute from, or if the window is empty."""
    location = _stream_location(conn, activity_id=activity_id)
    if location is None:
        raise ValueError(f"activity {activity_id!r} has no stream data to trim from")
    parquet_path, available_channels = location
    start_time_utc = _start_time_for(conn, athlete_id=athlete_id, activity_id=activity_id)

    effective_start = trim_start_s if trim_start_s is not None else 0.0
    effective_end = trim_end_s if trim_end_s is not None else float("inf")
    if effective_end <= effective_start:
        raise ValueError("trim_end_s must be after trim_start_s")

    window = _recompute_window(
        con,
        parquet_dir / parquet_path,
        available_channels,
        trim_start_s=effective_start,
        trim_end_s=effective_end,
    )
    if not window.points and window.duration_s is None:
        raise ValueError("no recorded samples remain in the requested trim window")

    now = datetime.now(UTC).replace(tzinfo=None)
    stmt = sqlite_insert(activity_trim_override).values(
        athlete_id=athlete_id,
        activity_start_time_utc=start_time_utc,
        trim_start_s=trim_start_s,
        trim_end_s=trim_end_s,
        created_at=now,
        updated_at=now,
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["athlete_id", "activity_start_time_utc"],
            set_={"trim_start_s": trim_start_s, "trim_end_s": trim_end_s, "updated_at": now},
        )
    )

    _write_window_to_activity(conn, athlete_id=athlete_id, activity_id=activity_id, window=window)
    _clip_laps(
        conn,
        con,
        parquet_dir / parquet_path,
        available_channels,
        athlete_id=athlete_id,
        activity_id=activity_id,
        trim_start_s=effective_start,
        trim_end_s=effective_end,
    )


def _clip_laps(
    conn: Connection,
    con: duckdb.DuckDBPyConnection,
    parquet_path: Path,
    available_channels: frozenset[str],
    *,
    athlete_id: str,
    activity_id: str,
    trim_start_s: float,
    trim_end_s: float,
) -> None:
    """A lap entirely outside the kept window is deleted (it no longer exists in the trimmed
    recording at all); a lap partially overlapping it is clipped to its own overlap sub-window,
    recomputed via the exact same `_recompute_window` the whole activity itself uses. Lap
    boundaries are relative to the *activity's own original start*, same as `trim_start_s`/
    `trim_end_s` -- `lap.start_time_utc` minus the activity's own start gives each lap's own
    elapsed-time bounds without needing a second Parquet read per lap."""
    activity_start = _start_time_for(conn, athlete_id=athlete_id, activity_id=activity_id)
    laps = conn.execute(
        select(lap.c.id, lap.c.start_time_utc, lap.c.duration_s).where(
            lap.c.athlete_id == athlete_id, lap.c.activity_id == activity_id
        )
    ).fetchall()
    for lap_row in laps:
        lap_start_s = (lap_row.start_time_utc - activity_start).total_seconds()
        lap_end_s = lap_start_s + (lap_row.duration_s or 0.0)
        overlap_start = max(lap_start_s, trim_start_s)
        overlap_end = min(lap_end_s, trim_end_s)
        if overlap_end <= overlap_start:
            conn.execute(delete(lap).where(lap.c.id == lap_row.id))
            continue
        window = _recompute_window(
            con,
            parquet_path,
            available_channels,
            trim_start_s=overlap_start,
            trim_end_s=overlap_end,
        )
        conn.execute(
            lap.update()
            .where(lap.c.id == lap_row.id)
            .values(
                distance_m=window.distance_m,
                duration_s=window.duration_s,
                moving_duration_s=window.duration_s,
                avg_hr=window.avg_hr,
                max_hr=window.max_hr,
                avg_speed_mps=(
                    window.distance_m / window.duration_s
                    if window.distance_m is not None and window.duration_s
                    else None
                ),
            )
        )


def clear_activity_trim(
    conn: Connection, archive_root: Path, *, athlete_id: str, activity_id: str
) -> None:
    """Removes the trim and fully restores the pristine pre-trim state -- see the module
    docstring for why this reparses the original raw bytes rather than recomputing with no
    window (which could never bring calories/training load back). Raises `ValueError` if the
    activity has no trim to clear, or its primary source's raw bytes can't be found."""
    start_time_utc = _start_time_for(conn, athlete_id=athlete_id, activity_id=activity_id)
    result = conn.execute(
        delete(activity_trim_override).where(
            activity_trim_override.c.athlete_id == athlete_id,
            activity_trim_override.c.activity_start_time_utc == start_time_utc,
        )
    )
    if result.rowcount == 0:
        raise ValueError(f"activity {activity_id!r} has no active trim")

    activity_row = conn.execute(
        select(activity.c.primary_source).where(activity.c.id == activity_id)
    ).fetchone()
    if activity_row is None:
        raise ValueError(f"no activity {activity_id!r} for athlete {athlete_id!r}")

    source_row = conn.execute(
        select(raw_object.c.kind, raw_object.c.storage_path)
        .select_from(activity_source_link.join(raw_object))
        .where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.activity_id == activity_id,
            activity_source_link.c.source == activity_row.primary_source,
        )
    ).fetchone()
    if source_row is None:
        raise ValueError(
            f"activity {activity_id!r} has no raw source to restore from "
            f"(primary_source={activity_row.primary_source!r})"
        )

    content = read_raw_bytes(archive_root, source_row.storage_path)
    batch = reparse_raw_object(source_row.kind, content)
    if batch.activity is None:
        raise ValueError(f"activity {activity_id!r}'s raw bytes no longer parse as an activity")

    _restore_from_parsed(
        conn, athlete_id=athlete_id, activity_id=activity_id, parsed=batch.activity
    )


def _restore_from_parsed(
    conn: Connection, *, athlete_id: str, activity_id: str, parsed: CanonicalActivity
) -> None:
    """Restores only the fields `set_activity_trim` ever changes -- `activity`'s own summary
    columns, the avg/max-heart-rate and training-load `activity_metric` rows, `route_geom`, and
    every `lap` row (deleted and reinserted wholesale, since a trim may have deleted some laps
    entirely and this must undo that too). Everything else on the activity (its id, source
    links, workout structure, splits, the Parquet stream itself) was never touched by a trim in
    the first place, so there's nothing to restore for it."""
    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(
        activity.update()
        .where(activity.c.id == activity_id)
        .values(
            distance_m=parsed.distance_m,
            duration_s=parsed.duration_s,
            moving_duration_s=parsed.moving_duration_s,
            elevation_gain_m=parsed.elevation_gain_m,
            calories=parsed.calories,
            updated_at=now,
        )
    )

    metrics_by_key = {m.key: m for m in parsed.extra_metrics}
    for keys in (
        _AVG_HR_METRIC_KEYS,
        _MAX_HR_METRIC_KEYS,
        _TOTAL_DESCENT_METRIC_KEYS,
        (_TRAINING_LOAD_METRIC_KEY,),
    ):
        for key in keys:
            m = metrics_by_key.get(key)
            if m is None:
                continue
            existing = conn.execute(
                select(activity_metric.c.id).where(
                    activity_metric.c.athlete_id == athlete_id,
                    activity_metric.c.activity_id == activity_id,
                    activity_metric.c.metric_key == key,
                )
            ).fetchone()
            if existing is not None:
                conn.execute(
                    activity_metric.update()
                    .where(activity_metric.c.id == existing.id)
                    .values(value_num=m.value_num, value_text=m.value_text, unit=m.unit)
                )
            else:
                conn.execute(
                    activity_metric.insert().values(
                        athlete_id=athlete_id,
                        activity_id=activity_id,
                        metric_key=key,
                        value_num=m.value_num,
                        value_text=m.value_text,
                        unit=m.unit,
                        source="fit_folder",
                        created_at=now,
                    )
                )

    if parsed.route_start:
        encoded = polyline_codec.encode(parsed.route_points) if parsed.route_points else None
        bbox = parsed.route_bbox
        conn.execute(
            route_geom.update()
            .where(route_geom.c.activity_id == activity_id)
            .values(
                encoded_polyline=encoded,
                simplified_polyline=encoded,
                min_lat=bbox[0] if bbox else None,
                min_lng=bbox[1] if bbox else None,
                max_lat=bbox[2] if bbox else None,
                max_lng=bbox[3] if bbox else None,
                start_lat=parsed.route_start[0],
                start_lng=parsed.route_start[1],
                end_lat=parsed.route_end[0] if parsed.route_end else None,
                end_lng=parsed.route_end[1] if parsed.route_end else None,
            )
        )

    conn.execute(
        delete(lap).where(lap.c.athlete_id == athlete_id, lap.c.activity_id == activity_id)
    )
    for lap_row in parsed.laps:
        conn.execute(
            lap.insert().values(
                athlete_id=athlete_id,
                activity_id=activity_id,
                lap_index=lap_row.lap_index,
                start_time_utc=lap_row.start_time_utc,
                duration_s=lap_row.duration_s,
                moving_duration_s=lap_row.moving_duration_s,
                distance_m=lap_row.distance_m,
                avg_hr=lap_row.avg_hr,
                max_hr=lap_row.max_hr,
                avg_speed_mps=lap_row.avg_speed_mps,
            )
        )


def apply_activity_trim_overrides(
    conn: Connection, con: duckdb.DuckDBPyConnection, parquet_dir: Path, *, athlete_id: str
) -> int:
    """Re-applies every durable trim after `sync rebuild` -- called alongside
    `apply_bouldering_route_overrides`/`apply_sport_overrides`. Matches by exact
    `start_time_utc` equality, same as those. Returns the number of activities re-trimmed."""
    overrides = conn.execute(
        select(
            activity_trim_override.c.activity_start_time_utc,
            activity_trim_override.c.trim_start_s,
            activity_trim_override.c.trim_end_s,
        ).where(activity_trim_override.c.athlete_id == athlete_id)
    ).fetchall()
    corrected = 0
    for o in overrides:
        activity_row = conn.execute(
            select(activity.c.id).where(
                activity.c.athlete_id == athlete_id,
                activity.c.start_time_utc == o.activity_start_time_utc,
                activity.c.deleted_at.is_(None),
            )
        ).fetchone()
        if activity_row is None:
            continue
        try:
            set_activity_trim(
                conn,
                con,
                parquet_dir,
                athlete_id=athlete_id,
                activity_id=activity_row.id,
                trim_start_s=o.trim_start_s,
                trim_end_s=o.trim_end_s,
            )
        except ValueError:
            # No stream data (yet) for this replay pass, or the trim window no longer has any
            # samples -- skip rather than crash the whole rebuild; the durable record itself is
            # untouched, so a later rebuild (once the stream exists) picks it back up.
            continue
        corrected += 1
    return corrected
