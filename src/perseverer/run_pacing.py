"""How a run was paced: its first and second half, its start and its finish, computed once per run
from the per-second stream and stored as `activity_metric` rows, so the run insights
(`insights/rules_activity.py`) can compare a run with every earlier one without reading hundreds
of Parquet files per request.

Definitions (all paces in seconds per km, over *moving* time):
- **halves**: the first and second half of the run's distance; the second half faster than the
  first is a negative split.
- **start**: the first START_FINISH_M (2 km); **finish**: the last START_FINISH_M.
- **negative km splits**: how many kilometres of the second half were faster than the first
  half's average pace. The second half's kilometres are the full ones that start at or after the
  halfway point (a kilometre straddling the middle belongs to neither half, the final partial one
  is left out); paces are compared rounded to the second, so a tie is not counted.
- Only `sport == "running"` activities of at least MIN_DISTANCE_M (5 km) get paces, so the start
  and the finish never overlap and the halves are long enough to mean something.
- Moving time leaves out stops: samples that don't advance for MIN_STOP_S (5 s) or more, such as
  a traffic light or a gap in the recording while the watch was auto-paused, so a stop never
  makes a start look slow. This is the same moving-time basis as the "Fastest
  pace" record, so it can differ slightly from the activity page's Splits table (elapsed time).
- An active trim (`activity_trim_override`) is honoured: distance and time are measured inside the
  kept window only.

Incremental, unlike the full recomputes next to it at each import (`performance.py::refresh_vdot`):
reading every run's stream takes several seconds, too much for every sync. Each processed run gets
a SIGNATURE_KEY row whose `value_text` records the inputs it was computed from (PACING_VERSION,
stream file and trim window); a run is recomputed only when it has no signature yet or the
signature no longer matches (a trim, an undo, a merge that swapped the stream), and the rows of
activities that are no longer runs are removed. Runs shorter than 5 km keep a signature too, so
they aren't re-read.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from sqlalchemy import Connection, select

from perseverer.db.schema import activity, activity_metric, activity_stream, activity_trim_override
from perseverer.metrics.registry import get_or_register_metric

START_FINISH_M = 2000.0
MIN_DISTANCE_M = 5000.0
MIN_MOVING_SPEED_MPS = 0.5
MIN_STOP_S = 5.0
# Part of every run's signature: bump it when a definition above changes, and the next refresh
# recomputes every run.
PACING_VERSION = 3

FIRST_HALF_KEY = "perseverer.pacing.first_half_pace_s_per_km"
SECOND_HALF_KEY = "perseverer.pacing.second_half_pace_s_per_km"
START_KEY = "perseverer.pacing.start_pace_s_per_km"
FINISH_KEY = "perseverer.pacing.finish_pace_s_per_km"
NEGATIVE_KM_SPLITS_KEY = "perseverer.pacing.negative_km_splits"
SECOND_HALF_KM_KEY = "perseverer.pacing.second_half_km_count"
SIGNATURE_KEY = "perseverer.pacing.signature"
PACE_KEYS = (FIRST_HALF_KEY, SECOND_HALF_KEY, START_KEY, FINISH_KEY)
ALL_KEYS = (*PACE_KEYS, NEGATIVE_KM_SPLITS_KEY, SECOND_HALF_KM_KEY, SIGNATURE_KEY)

_SOURCE = "perseverer"
_DISPLAY_NAMES = {
    FIRST_HALF_KEY: "First-half pace",
    SECOND_HALF_KEY: "Second-half pace",
    START_KEY: "Start pace (first 2 km)",
    FINISH_KEY: "Finish pace (last 2 km)",
}


@dataclass(frozen=True)
class RunPacing:
    first_half_s_per_km: float
    second_half_s_per_km: float
    start_s_per_km: float
    finish_s_per_km: float
    negative_km_splits: int
    second_half_km_count: int


def compute_run_pacing(elapsed_s: np.ndarray, distance_m: np.ndarray) -> RunPacing | None:
    """Pure: paces from parallel arrays of elapsed seconds and cumulative metres (both already
    limited to the kept window). None for a run shorter than MIN_DISTANCE_M or with unusable data.
    """
    ok = ~(np.isnan(elapsed_s) | np.isnan(distance_m))
    t = elapsed_s[ok]
    d = distance_m[ok]
    if t.size < 2:
        return None
    # Cumulative distance can dip by GPS noise; a running maximum keeps it monotonic, which the
    # interpolation below needs.
    d = np.maximum.accumulate(d - d[0])
    total = float(d[-1])
    if total < MIN_DISTANCE_M:
        return None

    # A stop is a stretch of consecutive samples that don't advance (slower than
    # MIN_MOVING_SPEED_MPS) lasting MIN_STOP_S or more -- a traffic light, or one long gap while the
    # watch was auto-paused. A shorter stall is GPS distance not updating for a second while
    # running, and dropping it would make every pace look too fast.
    dt = np.diff(t)
    still = dt * MIN_MOVING_SPEED_MPS > np.diff(d)
    stopped = np.zeros_like(still)
    edges = np.diff(np.concatenate(([0], still.astype(np.int8), [0])))
    for begin, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
        if dt[begin:end].sum() >= MIN_STOP_S:
            stopped[begin:end] = True
    moving_t = np.concatenate(([0.0], np.cumsum(np.where(stopped | (dt <= 0), 0.0, dt))))

    def moving_time_at(distance: float) -> float:
        return float(np.interp(distance, d, moving_t))

    def pace(from_m: float, to_m: float) -> float | None:
        seconds = moving_time_at(to_m) - moving_time_at(from_m)
        if seconds <= 0:
            return None
        return seconds / ((to_m - from_m) / 1000.0)

    half = total / 2
    paces = (
        pace(0.0, half),
        pace(half, total),
        pace(0.0, START_FINISH_M),
        pace(total - START_FINISH_M, total),
    )
    if any(p is None for p in paces):
        return None
    first, second, start, finish = paces
    assert first is not None and second is not None and start is not None and finish is not None

    # 1 m of slack either way, so GPS rounding (a 10 km run measured 9,999.8 m) doesn't drop a
    # kilometre at the halfway point or at the end.
    km_starts = np.arange(np.ceil((half - 1.0) / 1000) * 1000.0, total - 999.0, 1000.0)
    km_paces = np.round(
        np.interp(km_starts + 1000.0, d, moving_t) - np.interp(km_starts, d, moving_t)
    )
    negative = int(np.sum(km_paces < round(first)))
    return RunPacing(first, second, start, finish, negative, int(km_starts.size))


def _read_stream(path: Path, window: tuple[float, float] | None) -> tuple[np.ndarray, np.ndarray]:
    table = pq.read_table(path, columns=["timestamp_utc", "distance_m"])
    ts = table.column("timestamp_utc").cast("int64").to_numpy(zero_copy_only=False)
    elapsed = (ts - ts[0]).astype(float) / 1_000_000 if ts.size else np.array([], dtype=float)
    distance = table.column("distance_m").to_numpy(zero_copy_only=False).astype(float)
    if window is not None:
        keep = (elapsed >= window[0]) & (elapsed <= window[1])
        elapsed, distance = elapsed[keep], distance[keep]
    return elapsed, distance


def _register_metrics(conn: Connection) -> None:
    for key, name in _DISPLAY_NAMES.items():
        get_or_register_metric(
            conn,
            metric_key=key,
            source=_SOURCE,
            display_name=name,
            unit_si="s/km",
            category="performance",
            value_type="numeric",
        )
    for key, name in (
        (NEGATIVE_KM_SPLITS_KEY, "Negative km splits"),
        (SECOND_HALF_KM_KEY, "Second-half kilometres"),
    ):
        get_or_register_metric(
            conn,
            metric_key=key,
            source=_SOURCE,
            display_name=name,
            unit_si=None,
            category="performance",
            value_type="numeric",
        )
    get_or_register_metric(
        conn,
        metric_key=SIGNATURE_KEY,
        source=_SOURCE,
        display_name="Pacing analysis inputs",
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


def refresh_run_pacing(conn: Connection, parquet_dir: Path, *, athlete_id: str) -> int:
    """Brings every run's pacing rows up to date; returns the number of runs (re)computed."""

    runs = conn.execute(
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
            activity.c.sport == "running",
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

    # Read and compute every changed run first, then write: the first write opens the write
    # transaction, and holding it while streams are read would block the API meanwhile.
    rows: list[dict[str, object]] = []
    changed: list[str] = []
    now = datetime.now(UTC).replace(tzinfo=None)
    for run in runs:
        window = trims.get(run.start_time_utc)
        signature = json.dumps(
            {
                "version": PACING_VERSION,
                "stream": run.parquet_path,
                "trim": list(window) if window else None,
            },
            sort_keys=True,
        )
        if signatures.get(run.id) == signature:
            continue
        changed.append(run.id)
        pacing: RunPacing | None = None
        path = parquet_dir / run.parquet_path
        if "distance_m" in json.loads(run.channels) and path.exists():
            elapsed, distance = _read_stream(path, window)
            pacing = compute_run_pacing(elapsed, distance)
        values: list[tuple[str, float | None, str | None, str | None]] = [
            (SIGNATURE_KEY, None, signature, None)
        ]
        if pacing is not None:
            values += [
                (FIRST_HALF_KEY, pacing.first_half_s_per_km, None, "s/km"),
                (SECOND_HALF_KEY, pacing.second_half_s_per_km, None, "s/km"),
                (START_KEY, pacing.start_s_per_km, None, "s/km"),
                (FINISH_KEY, pacing.finish_s_per_km, None, "s/km"),
                (NEGATIVE_KM_SPLITS_KEY, float(pacing.negative_km_splits), None, None),
                (SECOND_HALF_KM_KEY, float(pacing.second_half_km_count), None, None),
            ]
        rows += [
            {
                "athlete_id": athlete_id,
                "activity_id": run.id,
                "metric_key": key,
                "value_num": num,
                "value_text": text,
                "unit": unit,
                "source": _SOURCE,
                "created_at": now,
            }
            for key, num, text, unit in values
        ]

    run_ids = {r.id for r in runs}
    stale = [i for i in signatures if i not in run_ids]
    if not stale and not changed:
        return 0
    _register_metrics(conn)
    _delete(conn, athlete_id, [*stale, *changed])
    if rows:
        conn.execute(activity_metric.insert(), rows)
    return len(changed)
