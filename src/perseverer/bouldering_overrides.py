"""The athlete's own corrections to bouldering route data: overriding a route's status when the
device (or the reverse-engineered `climb_result` mapping, see `fit/parser.py`) got it wrong, and
adding a route the device never recorded at all (forgotten to start/stop tracking, a route
climbed after the watch was already stopped, etc.).

Same shape as `sport_override.py`'s own sport/race/name/fueling corrections, for the same reason:
`sync rebuild` wipes and re-derives both `activity` and `split` from raw bytes on every run,
minting a fresh `activity.id` and re-deriving every `split` row from scratch -- a bare `UPDATE
split SET climb_result = ...` would silently vanish on the next rebuild. `bouldering_route_status_
override`/`bouldering_manual_route` are the durable record instead (never wiped -- see
`rebuild.py`'s `_REBUILDABLE_TABLES`), and `apply_bouldering_route_overrides` re-applies them as a
correction pass at the end of every rebuild, mirroring `apply_sport_overrides` exactly.

Keyed by `(athlete_id, activity_start_time_utc, split_index)` for a status override -- not
`activity.id`, which isn't stable across a rebuild, but `start_time_utc` (parsed straight out of
the same archived bytes every time) and a FIT-derived split's own `split_index` (its position in
file order, equally stable) both are. A manually-added route has no FIT-derived `split_index` to
key off of at all, so it gets its own independent `manual_order` sequence instead (0, 1, 2, ...
per activity, assigned once at creation and never changed) -- its *live* `split_index` isn't
stable the same way (recomputed fresh after every rebuild), so `bouldering_manual_route` keeps a
synced copy of it, updated every time its own `split` row is (re-)inserted, which is what lets a
delete find that row directly instead of guessing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from perseverer.db.schema import (
    activity,
    bouldering_manual_route,
    bouldering_route_status_override,
    kaya_dismissed_effort,
    split,
)

# The only two confirmed raw FIT result values (see fit/parser.py's own docstring) -- an
# athlete-entered override is real, fresh input, not a reverse-engineered guess, so it's held to
# a stricter contract than the FIT decode itself (which must tolerate an unrecognized raw value).
VALID_RESULTS = frozenset({"attempt", "completed"})


def _start_time_for(conn: Connection, *, athlete_id: str, activity_id: str) -> datetime:
    row = conn.execute(
        select(activity.c.start_time_utc).where(
            activity.c.id == activity_id, activity.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise ValueError(f"no activity {activity_id!r} for athlete {athlete_id!r}")
    return cast(datetime, row.start_time_utc)


def set_route_status_override(
    conn: Connection, *, athlete_id: str, activity_id: str, split_index: int, result: str
) -> None:
    """Records the correction (upserting on the athlete/start_time_utc/split_index identity, so
    re-fixing an already-corrected route just replaces the previous correction) and applies it to
    the live `split` row immediately, rather than waiting for the next `sync rebuild`. Raises
    `ValueError` if the activity doesn't exist, or if `split_index` doesn't identify a real
    climb_active route (a rest interval, or an index that doesn't exist, has no status to
    override)."""
    if result not in VALID_RESULTS:
        raise ValueError(f"result must be one of {sorted(VALID_RESULTS)}, got {result!r}")
    start_time_utc = _start_time_for(conn, athlete_id=athlete_id, activity_id=activity_id)

    split_row = conn.execute(
        select(split.c.split_type).where(
            split.c.athlete_id == athlete_id,
            split.c.activity_id == activity_id,
            split.c.split_index == split_index,
        )
    ).fetchone()
    if split_row is None or split_row.split_type != "climb_active":
        raise ValueError(
            f"activity {activity_id!r} has no climb_active route at split_index {split_index}"
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    stmt = sqlite_insert(bouldering_route_status_override).values(
        athlete_id=athlete_id,
        activity_start_time_utc=start_time_utc,
        split_index=split_index,
        result=result,
        created_at=now,
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["athlete_id", "activity_start_time_utc", "split_index"],
            set_={"result": result, "created_at": now},
        )
    )
    conn.execute(
        split.update()
        .where(
            split.c.athlete_id == athlete_id,
            split.c.activity_id == activity_id,
            split.c.split_index == split_index,
        )
        .values(climb_result=result)
    )


def set_route_grade_override(
    conn: Connection, *, athlete_id: str, activity_id: str, split_index: int, grade: int
) -> None:
    """Corrects a route's grade -- for a FIT-derived route this is the same durable-override
    shape as `set_route_status_override` (upserting just the `grade` column, leaving any
    separately-recorded status correction on that same row untouched). For a manually-added
    route there's no separate override table to write: `bouldering_manual_route` is already that
    route's own durable record, so this updates its `grade` column directly instead -- the next
    `apply_bouldering_route_overrides` picks up the corrected value for free, through the same
    manual-route reinsertion it already does, no separate reapply path needed. Raises
    `ValueError` if grade is negative or split_index doesn't identify a real climb_active route.
    """
    if grade < 0:
        raise ValueError(f"grade must be non-negative, got {grade!r}")
    start_time_utc = _start_time_for(conn, athlete_id=athlete_id, activity_id=activity_id)

    split_row = conn.execute(
        select(split.c.split_type, split.c.is_manual).where(
            split.c.athlete_id == athlete_id,
            split.c.activity_id == activity_id,
            split.c.split_index == split_index,
        )
    ).fetchone()
    if split_row is None or split_row.split_type != "climb_active":
        raise ValueError(
            f"activity {activity_id!r} has no climb_active route at split_index {split_index}"
        )

    if split_row.is_manual:
        result = conn.execute(
            bouldering_manual_route.update()
            .where(
                bouldering_manual_route.c.athlete_id == athlete_id,
                bouldering_manual_route.c.activity_start_time_utc == start_time_utc,
                bouldering_manual_route.c.split_index == split_index,
            )
            .values(grade=grade)
        )
        if result.rowcount == 0:
            raise ValueError(
                f"activity {activity_id!r} has no manual-route record at split_index {split_index}"
            )
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        stmt = sqlite_insert(bouldering_route_status_override).values(
            athlete_id=athlete_id,
            activity_start_time_utc=start_time_utc,
            split_index=split_index,
            grade=grade,
            created_at=now,
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["athlete_id", "activity_start_time_utc", "split_index"],
                set_={"grade": grade, "created_at": now},
            )
        )

    conn.execute(
        split.update()
        .where(
            split.c.athlete_id == athlete_id,
            split.c.activity_id == activity_id,
            split.c.split_index == split_index,
        )
        .values(climb_grade=grade)
    )


def _next_split_index(conn: Connection, *, athlete_id: str, activity_id: str) -> int:
    return cast(
        int,
        conn.execute(
            select(func.coalesce(func.max(split.c.split_index), -1) + 1).where(
                split.c.athlete_id == athlete_id, split.c.activity_id == activity_id
            )
        ).scalar_one(),
    )


def add_manual_route(
    conn: Connection, *, athlete_id: str, activity_id: str, grade: int, result: str
) -> int:
    """Records a route the device never tracked at all, and inserts its live `split` row
    immediately (so it appears without waiting for a rebuild). Returns the new row's
    `split_index`. Raises `ValueError` if the activity doesn't exist or `result` is invalid."""
    if result not in VALID_RESULTS:
        raise ValueError(f"result must be one of {sorted(VALID_RESULTS)}, got {result!r}")
    start_time_utc = _start_time_for(conn, athlete_id=athlete_id, activity_id=activity_id)

    next_manual_order = conn.execute(
        select(func.coalesce(func.max(bouldering_manual_route.c.manual_order), -1) + 1).where(
            bouldering_manual_route.c.athlete_id == athlete_id,
            bouldering_manual_route.c.activity_start_time_utc == start_time_utc,
        )
    ).scalar_one()
    split_index = _next_split_index(conn, athlete_id=athlete_id, activity_id=activity_id)

    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(
        bouldering_manual_route.insert().values(
            athlete_id=athlete_id,
            activity_start_time_utc=start_time_utc,
            manual_order=next_manual_order,
            grade=grade,
            result=result,
            split_index=split_index,
            created_at=now,
        )
    )
    conn.execute(
        split.insert().values(
            athlete_id=athlete_id,
            activity_id=activity_id,
            split_index=split_index,
            split_type="climb_active",
            climb_grade=grade,
            climb_result=result,
            is_manual=True,
        )
    )
    return split_index


def delete_manual_route(
    conn: Connection, *, athlete_id: str, activity_id: str, split_index: int
) -> None:
    """Removes a manually-added route -- both its durable record (so it doesn't come back on the
    next rebuild) and its live `split` row. Raises `ValueError` if `split_index` doesn't identify
    a manually-added route on this activity (a FIT-derived route is never deletable this way --
    it would just be re-derived by the next ingest/rebuild regardless)."""
    start_time_utc = _start_time_for(conn, athlete_id=athlete_id, activity_id=activity_id)

    result = conn.execute(
        bouldering_manual_route.delete().where(
            bouldering_manual_route.c.athlete_id == athlete_id,
            bouldering_manual_route.c.activity_start_time_utc == start_time_utc,
            bouldering_manual_route.c.split_index == split_index,
        )
    )
    if result.rowcount == 0:
        raise ValueError(
            f"activity {activity_id!r} has no manually-added route at split_index {split_index}"
        )
    conn.execute(
        split.delete().where(
            split.c.athlete_id == athlete_id,
            split.c.activity_id == activity_id,
            split.c.split_index == split_index,
        )
    )


def dismiss_garmin_extra_route(
    conn: Connection, *, athlete_id: str, activity_id: str, split_index: int
) -> None:
    """Removes a watch effort that the Kaya merge added back as an extra route
    (`source="garmin_extra"`, see `adapters/kaya_ingest.py`) from the route list. The effort is
    remembered in `kaya_dismissed_effort` by its own start time, so later Kaya syncs and rebuilds
    leave it out too, and its live row goes back to a superseded effort: its duration and heart
    rate still count toward climb time, it is just no longer a route. Raises `ValueError` if
    `split_index` doesn't identify a garmin_extra route on this activity."""
    start_time_utc = _start_time_for(conn, athlete_id=athlete_id, activity_id=activity_id)
    row = conn.execute(
        select(split.c.id, split.c.start_time_utc).where(
            split.c.athlete_id == athlete_id,
            split.c.activity_id == activity_id,
            split.c.split_index == split_index,
            split.c.split_type == "climb_active",
            split.c.source == "garmin_extra",
        )
    ).fetchone()
    if row is None or row.start_time_utc is None:
        raise ValueError(
            f"activity {activity_id!r} has no watch-only route at split_index {split_index}"
        )
    conn.execute(
        sqlite_insert(kaya_dismissed_effort)
        .values(
            athlete_id=athlete_id,
            activity_start_time_utc=start_time_utc,
            effort_start_time_utc=row.start_time_utc,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        .on_conflict_do_nothing()
    )
    conn.execute(
        split.update()
        .where(split.c.id == row.id)
        .values(
            split_type="climb_active_superseded",
            climb_grade=None,
            climb_result=None,
            source=None,
        )
    )


def apply_bouldering_route_overrides(conn: Connection, *, athlete_id: str) -> int:
    """Re-applies every recorded route-status/grade correction and re-inserts every manually-
    added route -- called at the end of `sync rebuild` so all three survive a full wipe-and-
    replay. Corrections match by exact `(start_time_utc, split_index)` equality (both sides come
    from the same archived bytes, identical down to the second) -- only the columns actually
    recorded on a given override row are written, same as `apply_sport_overrides`, so a grade-
    only correction never overwrites a separately-recorded status back to null and vice versa.
    Manual routes are re-appended in `manual_order` sequence, exactly as `add_manual_route`
    inserts them the first time -- each one's freshly (re-)computed live `split_index` is written
    back onto its own durable row, so a later delete/grade-edit can still find it directly.
    Returns the number of splits corrected or added.
    """
    changed = 0

    status_overrides = conn.execute(
        select(
            bouldering_route_status_override.c.activity_start_time_utc,
            bouldering_route_status_override.c.split_index,
            bouldering_route_status_override.c.result,
            bouldering_route_status_override.c.grade,
        ).where(bouldering_route_status_override.c.athlete_id == athlete_id)
    ).fetchall()
    for o in status_overrides:
        if o.result is None and o.grade is None:
            continue
        activity_row = conn.execute(
            select(activity.c.id).where(
                activity.c.athlete_id == athlete_id,
                activity.c.start_time_utc == o.activity_start_time_utc,
                activity.c.deleted_at.is_(None),
            )
        ).fetchone()
        if activity_row is None:
            continue
        values: dict[str, object] = {}
        if o.result is not None:
            values["climb_result"] = o.result
        if o.grade is not None:
            values["climb_grade"] = o.grade
        result = conn.execute(
            split.update()
            .where(
                split.c.athlete_id == athlete_id,
                split.c.activity_id == activity_row.id,
                split.c.split_index == o.split_index,
                split.c.split_type == "climb_active",
            )
            .values(**values)
        )
        changed += result.rowcount

    manual_routes = conn.execute(
        select(
            bouldering_manual_route.c.id,
            bouldering_manual_route.c.activity_start_time_utc,
            bouldering_manual_route.c.grade,
            bouldering_manual_route.c.result,
        )
        .where(bouldering_manual_route.c.athlete_id == athlete_id)
        .order_by(
            bouldering_manual_route.c.activity_start_time_utc,
            bouldering_manual_route.c.manual_order,
        )
    ).fetchall()
    for r in manual_routes:
        activity_row = conn.execute(
            select(activity.c.id).where(
                activity.c.athlete_id == athlete_id,
                activity.c.start_time_utc == r.activity_start_time_utc,
                activity.c.deleted_at.is_(None),
            )
        ).fetchone()
        if activity_row is None:
            continue
        split_index = _next_split_index(conn, athlete_id=athlete_id, activity_id=activity_row.id)
        conn.execute(
            split.insert().values(
                athlete_id=athlete_id,
                activity_id=activity_row.id,
                split_index=split_index,
                split_type="climb_active",
                climb_grade=r.grade,
                climb_result=r.result,
                is_manual=True,
            )
        )
        conn.execute(
            bouldering_manual_route.update()
            .where(bouldering_manual_route.c.id == r.id)
            .values(split_index=split_index)
        )
        changed += 1

    return changed
