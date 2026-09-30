"""Kaya ingestion: fetch -> archive raw -> parse into kaya_session/kaya_ascent -> derive bouldering
activities and route splits (ADR 0016).

Three separable steps, so `sync rebuild` can replay everything from the raw archive alone:

1. `fetch_pages` -- pages the two GraphQL queries (never falls back to credentials; a 429 aborts).
2. `store_page` -- a pure parse of one archived page, upserted by Kaya id. Idempotent.
3. `apply_kaya_sessions` -- derives `activity`/`split`/`activity_source_link` rows from the stored
   Kaya tables. Idempotent, and re-run at the end of every rebuild.

Combining with Garmin: Kaya session times are unreliable (a session is logged after the fact in a
burst), so a Kaya session is matched to a Garmin bouldering activity on the same *local date*, not
by time overlap. Exactly one Garmin candidate that day -> Kaya's route list replaces that
activity's *completed* route splits (Kaya is the more accurate source for sends; Garmin keeps
duration/HR/calories and its failed-attempt rows, which Kaya's feed lacks).
Zero or several candidates -> each Kaya session with routes becomes its own Kaya-only activity.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import Connection, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from perseverer.adapters import kaya
from perseverer.adapters.fit_folder import _derive_activity_id
from perseverer.archive import archive_raw_bytes
from perseverer.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    athlete,
    insight,
    kaya_ascent,
    kaya_session,
    split,
)
from perseverer.insights.engine import refresh_insights
from perseverer.rollups import refresh_daily_and_period_rollups

logger = logging.getLogger(__name__)

SOURCE = "kaya"
KIND_SESSIONS = "kaya_sessions_json"
KIND_ASCENTS = "kaya_ascents_json"
PAGE_SIZE = 50
_REQUEST_INTERVAL_S = 0.5
_MAX_PAGES = 400

SESSIONS_QUERY = (
    "query sessionsForUser($user_id: ID!, $offset: Int!, $count: Int!) { "
    "sessionsForUser(user_id: $user_id, offset: $offset, count: $count) { "
    "id start_time end_time notes gym { id name address city region country latitude longitude } "
    "board { id name latitude longitude } destination { id name latitude longitude } } }"
)
ASCENTS_QUERY = (
    "query ascentsForUser($user_id: ID!, $offset: Int!, $count: Int!) { "
    "ascentsForUser(user_id: $user_id, offset: $offset, count: $count) { "
    "id session_id date comment rating stiffness attempts ascent_type { id name } "
    "gym { id name address city region country latitude longitude } "
    "climb { id name lead climb_type { id name } grade { id name climb_type_group } "
    "gym { id name address city region country latitude longitude } } } }"
)

_GRADE_RE = re.compile(r"^v(\d+)$", re.IGNORECASE)


class KayaRateLimited(Exception):
    """Kaya answered 429; the run stops immediately, no retry."""


def grade_to_v(grade_name: str | None) -> int | None:
    """Kaya's "v3" -> 3. Anything not a plain V-grade is None -- never guessed."""
    if not grade_name:
        return None
    m = _GRADE_RE.match(grade_name.strip())
    return int(m.group(1)) if m else None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


# --- step 1: fetch ---------------------------------------------------------------------------


def fetch_pages(tokenstore_dir: Path) -> list[tuple[str, bytes]]:
    """Every page of both queries, as (raw kind, response bytes). Refreshes the access token once
    on a 401/403; a 429 raises `KayaRateLimited`."""
    tokens = kaya.load_tokens(tokenstore_dir)
    out: list[tuple[str, bytes]] = []
    for kind, query in ((KIND_SESSIONS, SESSIONS_QUERY), (KIND_ASCENTS, ASCENTS_QUERY)):
        offset = 0
        for _ in range(_MAX_PAGES):
            payload = {
                "query": query,
                "variables": {"user_id": tokens.user_id, "offset": offset, "count": PAGE_SIZE},
            }
            response = _gql(payload, tokens.token)
            if response.status_code in (401, 403):
                tokens = kaya.refresh_access_token(tokenstore_dir)
                response = _gql(payload, tokens.token)
            if response.status_code == 429:
                raise KayaRateLimited("Kaya rate limit hit; try again later.")
            response.raise_for_status()
            content = response.content
            out.append((kind, content))
            rows = _rows(json.loads(content))
            if len(rows) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            time.sleep(_REQUEST_INTERVAL_S)
    return out


def _gql(payload: dict[str, Any], token: str) -> requests.Response:
    return requests.post(
        f"{kaya.KAYA_API_BASE}/graphql",
        json=payload,
        headers=kaya._headers(token),
        timeout=kaya.REQUEST_TIMEOUT_S,
    )


def _rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    if body.get("errors"):
        raise RuntimeError(f"Kaya GraphQL error: {json.dumps(body['errors'])[:300]}")
    data = body.get("data") or {}
    rows = next(iter(data.values()), None)
    return list(rows or [])


# --- step 2: parse + store -------------------------------------------------------------------


def store_page(
    conn: Connection, *, athlete_id: str, raw_object_id: int, kind: str, content: bytes
) -> int:
    """Upserts one archived page into kaya_session / kaya_ascent. Returns rows written."""
    rows = _rows(json.loads(content))
    count = 0
    for r in rows:
        if kind == KIND_SESSIONS:
            start = _parse_time(r.get("start_time"))
            if start is None:
                continue
            gym = r.get("gym") or {}
            values: dict[str, Any] = {
                "athlete_id": athlete_id,
                "kaya_id": str(r["id"]),
                "start_time_utc": start,
                "end_time_utc": _parse_time(r.get("end_time")),
                "notes": r.get("notes"),
                "gym_name": gym.get("name"),
                "gym_city": gym.get("city"),
                "raw_object_id": raw_object_id,
            }
            stmt = sqlite_insert(kaya_session).values(**values)
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["athlete_id", "kaya_id"],
                    set_={k: v for k, v in values.items() if k not in ("athlete_id", "kaya_id")},
                )
            )
        else:
            when = _parse_time(r.get("date"))
            if when is None:
                continue
            climb = r.get("climb") or {}
            values = {
                "athlete_id": athlete_id,
                "kaya_id": str(r["id"]),
                "session_kaya_id": str(r.get("session_id")),
                "date_utc": when,
                "ascent_type": (r.get("ascent_type") or {}).get("name"),
                "grade_name": (climb.get("grade") or {}).get("name"),
                "climb_kaya_id": str(climb["id"]) if climb.get("id") is not None else None,
                "climb_name": climb.get("name"),
                "climb_type": (climb.get("climb_type") or {}).get("name"),
                "is_lead": climb.get("lead"),
                "attempts": r.get("attempts"),
                "rating": r.get("rating"),
                "comment": r.get("comment"),
                "raw_object_id": raw_object_id,
            }
            stmt = sqlite_insert(kaya_ascent).values(**values)
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["athlete_id", "kaya_id"],
                    set_={k: v for k, v in values.items() if k not in ("athlete_id", "kaya_id")},
                )
            )
        count += 1
    return count


# --- step 3: derive activities + splits ------------------------------------------------------


@dataclass
class _Route:
    when: datetime
    grade: int | None
    ascent_type: str | None


def _athlete_tz(conn: Connection, athlete_id: str) -> ZoneInfo:
    name = conn.execute(select(athlete.c.timezone).where(athlete.c.id == athlete_id)).scalar()
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _delete_activity(conn: Connection, athlete_id: str, activity_id: str) -> None:
    conn.execute(delete(insight).where(insight.c.activity_id == activity_id))
    conn.execute(delete(activity_metric).where(activity_metric.c.activity_id == activity_id))
    conn.execute(delete(split).where(split.c.activity_id == activity_id))
    conn.execute(
        delete(activity_source_link).where(activity_source_link.c.activity_id == activity_id)
    )
    conn.execute(delete(activity).where(activity.c.id == activity_id))


def _replace_splits(
    conn: Connection,
    athlete_id: str,
    activity_id: str,
    routes: list[_Route],
    *,
    keep_garmin_attempts: bool,
) -> None:
    """Writes Kaya's routes as climb_active splits. For a Garmin activity only its *completed*
    splits are replaced (Kaya is authoritative for sends); Garmin's attempt / unknown rows are
    kept, because Kaya's ascent feed has no failed attempts at all. A Kaya-only activity is
    rewritten from scratch."""
    where = [split.c.athlete_id == athlete_id, split.c.activity_id == activity_id]
    if keep_garmin_attempts:
        where.append(split.c.climb_result == "completed")
    conn.execute(delete(split).where(*where))
    next_index = (
        conn.execute(
            select(func.coalesce(func.max(split.c.split_index), -1) + 1).where(
                split.c.athlete_id == athlete_id, split.c.activity_id == activity_id
            )
        ).scalar_one()
        if keep_garmin_attempts
        else 0
    )
    for i, route in enumerate(sorted(routes, key=lambda x: x.when)):
        conn.execute(
            split.insert().values(
                athlete_id=athlete_id,
                activity_id=activity_id,
                split_index=next_index + i,
                split_type="climb_active",
                start_time_utc=route.when,
                climb_grade=route.grade,
                # Flash/Onsight/Redpoint/Repeat are all a completed top.
                climb_result="completed",
            )
        )


def _link(
    conn: Connection,
    athlete_id: str,
    activity_id: str,
    kaya_session_id: str,
    raw_id: int,
    now: datetime,
) -> None:
    stmt = sqlite_insert(activity_source_link).values(
        athlete_id=athlete_id,
        activity_id=activity_id,
        source=SOURCE,
        external_id=f"session:{kaya_session_id}",
        raw_object_id=raw_id,
        ingested_at=now,
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["athlete_id", "source", "external_id"],
            set_={"activity_id": activity_id, "raw_object_id": raw_id, "ingested_at": now},
        )
    )


def apply_kaya_sessions(conn: Connection, *, athlete_id: str) -> set[str]:
    """Derives activities/splits from the stored Kaya tables. Returns the local dates touched
    (for the caller's rollup refresh). Idempotent."""
    tz = _athlete_tz(conn, athlete_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    sessions = conn.execute(
        select(kaya_session).where(kaya_session.c.athlete_id == athlete_id)
    ).fetchall()
    ascents = conn.execute(
        select(kaya_ascent).where(
            kaya_ascent.c.athlete_id == athlete_id,
            kaya_ascent.c.climb_type == "Bouldering",
        )
    ).fetchall()
    routes_by_session: dict[str, list[_Route]] = defaultdict(list)
    for a in ascents:
        routes_by_session[a.session_kaya_id].append(
            _Route(a.date_utc, grade_to_v(a.grade_name), a.ascent_type)
        )

    by_date: dict[str, list[Any]] = defaultdict(list)
    offsets: dict[str, int] = {}
    for s in sessions:
        local = s.start_time_utc.replace(tzinfo=UTC).astimezone(tz)
        d = local.date().isoformat()
        by_date[d].append(s)
        offsets[s.kaya_id] = int(local.utcoffset().total_seconds()) if local.utcoffset() else 0

    touched: set[str] = set()
    for d, group in by_date.items():
        candidates = conn.execute(
            select(activity.c.id).where(
                activity.c.athlete_id == athlete_id,
                activity.c.deleted_at.is_(None),
                activity.c.local_date == d,
                activity.c.sport == "rock_climbing",
                activity.c.sub_sport == "bouldering",
                activity.c.primary_source != SOURCE,
            )
        ).fetchall()
        standalone_ids = {
            s.kaya_id: _derive_activity_id(athlete_id, SOURCE, f"session:{s.kaya_id}")
            for s in group
        }
        if len(candidates) == 1:
            target = candidates[0].id
            routes = [r for s in group for r in routes_by_session.get(s.kaya_id, [])]
            for s in group:
                _delete_activity(conn, athlete_id, standalone_ids[s.kaya_id])
            if routes:
                _replace_splits(conn, athlete_id, target, routes, keep_garmin_attempts=True)
                for s in group:
                    _link(conn, athlete_id, target, s.kaya_id, s.raw_object_id, now)
                touched.add(d)
            continue
        if len(candidates) > 1:
            logger.warning(
                "kaya: %d Garmin bouldering activities on %s; not merging, importing standalone",
                len(candidates),
                d,
            )
        for s in group:
            routes = routes_by_session.get(s.kaya_id, [])
            act_id = standalone_ids[s.kaya_id]
            if not routes:
                _delete_activity(conn, athlete_id, act_id)
                continue
            values = {
                "athlete_id": athlete_id,
                "start_time_utc": s.start_time_utc,
                "utc_offset_s": offsets[s.kaya_id],
                "local_date": d,
                "sport": "rock_climbing",
                "sub_sport": "bouldering",
                "name": s.gym_name or "Bouldering",
                "primary_source": SOURCE,
                "updated_at": now,
                "deleted_at": None,
            }
            existing = conn.execute(select(activity.c.id).where(activity.c.id == act_id)).first()
            if existing is None:
                conn.execute(activity.insert().values(id=act_id, created_at=now, **values))
            else:
                conn.execute(activity.update().where(activity.c.id == act_id).values(**values))
            _replace_splits(conn, athlete_id, act_id, routes, keep_garmin_attempts=False)
            _link(conn, athlete_id, act_id, s.kaya_id, s.raw_object_id, now)
            touched.add(d)
    return touched


# --- the import entry point ------------------------------------------------------------------


@dataclass
class KayaImportSummary:
    pages: int = 0
    sessions: int = 0
    ascents: int = 0
    touched_dates: int = 0


def import_kaya(
    conn: Connection,
    archive_root: Path,
    *,
    athlete_id: str,
    tokenstore_dir: Path,
) -> KayaImportSummary:
    """Fetch all pages, archive each raw, store, then derive activities and refresh rollups."""
    summary = KayaImportSummary()
    for kind, content in fetch_pages(tokenstore_dir):
        raw_id = archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE,
            kind=kind,
            content=content,
            locator=f"{kaya.KAYA_API_BASE}/graphql",
            http_status=200,
        )
        written = store_page(
            conn, athlete_id=athlete_id, raw_object_id=raw_id, kind=kind, content=content
        )
        summary.pages += 1
        if kind == KIND_SESSIONS:
            summary.sessions += written
        else:
            summary.ascents += written
        conn.commit()
    touched = apply_kaya_sessions(conn, athlete_id=athlete_id)
    conn.commit()
    if touched:
        refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched)
        refresh_insights(conn, athlete_id=athlete_id)
        conn.commit()
    summary.touched_dates = len(touched)
    return summary


__all__ = [
    "KayaImportSummary",
    "KayaRateLimited",
    "apply_kaya_sessions",
    "grade_to_v",
    "import_kaya",
    "store_page",
]
