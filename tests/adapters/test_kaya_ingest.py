"""Kaya ingest: parse/store idempotency, grade mapping, and the local-date merge with Garmin
bouldering activities (ADR 0016)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, select

from perseverer.adapters.kaya_ingest import (
    KIND_ASCENTS,
    KIND_SESSIONS,
    KIND_UNSENT,
    apply_kaya_sessions,
    grade_to_v,
    route_label,
    store_page,
)
from perseverer.archive import archive_raw_bytes
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, athlete, metadata, split
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="America/Los_Angeles",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    return engine


def _session(sid: str, start: str, attempted: list[str] | None = None) -> dict[str, Any]:
    return {
        "attempted_climbs": [
            {
                "id": f"{sid}_{i}",
                "name": None,
                "lead": False,
                "climb_type": {"id": "1", "name": "Bouldering"},
                "grade": {"id": "1", "name": g, "climb_type_group": "3"},
            }
            for i, g in enumerate(attempted or [])
        ],
        "id": sid,
        "start_time": start,
        "end_time": start,
        "notes": None,
        "gym": {"id": "1", "name": "Movement Santa Clara", "city": "Santa Clara"},
        "board": None,
        "destination": None,
    }


def _ascent(aid: str, sid: str, when: str, grade: str, kind: str = "Flash") -> dict[str, Any]:
    return {
        "id": aid,
        "session_id": sid,
        "date": when,
        "comment": None,
        "rating": None,
        "stiffness": 0,
        "attempts": None,
        "ascent_type": {"id": "1", "name": kind},
        "climb": {
            "id": f"c{aid}",
            "name": None,
            "lead": False,
            "climb_type": {"id": "1", "name": "Bouldering"},
            "grade": {"id": "1", "name": grade, "climb_type_group": "3"},
        },
    }


def _load(conn: Connection, tmp_path: Path, kind: str, key: str, rows: list[Any]) -> None:
    content = json.dumps({"data": {key: rows}}).encode()
    raw_id = archive_raw_bytes(
        conn,
        tmp_path / "raw",
        athlete_id=DEFAULT_ATHLETE_ID,
        source="kaya",
        kind=kind,
        content=content,
    )
    store_page(
        conn, athlete_id=DEFAULT_ATHLETE_ID, raw_object_id=raw_id, kind=kind, content=content
    )


def _garmin_boulder(conn: Connection, act_id: str, local_date: str) -> None:
    start = dt.datetime.fromisoformat(local_date + "T02:00:00")
    conn.execute(
        activity.insert().values(
            id=act_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=-25200,
            local_date=local_date,
            sport="rock_climbing",
            sub_sport="bouldering",
            duration_s=3600.0,
            primary_source="garmin_connect",
            created_at=start,
            updated_at=start,
        )
    )
    conn.execute(
        split.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=act_id,
            split_index=0,
            split_type="climb_active",
            climb_grade=9,
            climb_result="attempt",
        )
    )


def test_grade_to_v() -> None:
    assert grade_to_v("v3") == 3
    assert grade_to_v("V10") == 10
    assert grade_to_v("6B+") is None
    assert grade_to_v(None) is None


def test_kaya_only_session_becomes_activity(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        # 2026-09-27T03:00Z is still 2026-09-26 evening in Los Angeles.
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-09-27T03:00:00.000Z")],
        )
        _load(
            conn,
            tmp_path,
            KIND_ASCENTS,
            "ascentsForUser",
            [
                _ascent("a1", "s1", "2026-09-27T03:05:00.000Z", "v2"),
                _ascent("a2", "s1", "2026-09-27T03:10:00.000Z", "v3", "Repeat"),
            ],
        )
        touched = apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert touched == {"2026-09-26"}
        act = conn.execute(select(activity)).one()
        assert (act.primary_source, act.local_date, act.sub_sport) == (
            "kaya",
            "2026-09-26",
            "bouldering",
        )
        splits = conn.execute(select(split).order_by(split.c.split_index)).fetchall()
        assert [(s.climb_grade, s.climb_result) for s in splits] == [
            (2, "completed"),
            (3, "completed"),
        ]

        # Idempotent: a second pass changes nothing.
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert len(conn.execute(select(activity)).fetchall()) == 1
        assert len(conn.execute(select(split)).fetchall()) == 2


def test_kaya_routes_replace_garmin_splits_on_same_local_date(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")
        # Logged 04:08Z on 05-14 = 21:08 on 05-13 in LA... use a time that stays on 05-14 local.
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-05-14T20:00:00.000Z")],
        )
        _load(
            conn,
            tmp_path,
            KIND_ASCENTS,
            "ascentsForUser",
            [_ascent("a1", "s1", "2026-05-14T20:01:00.000Z", "v1")],
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert len(conn.execute(select(activity)).fetchall()) == 1  # merged, no second activity
        splits = conn.execute(select(split)).fetchall()
        # Garmin's attempt row survives; its completed rows would be replaced by Kaya's send.
        assert sorted((s.climb_grade, s.climb_result) for s in splits) == [
            (1, "completed"),
            (9, "attempt"),
        ]
        assert sorted(s.split_index for s in splits) == [0, 1]
        # Re-applying is idempotent (Kaya's send is replaced, not duplicated).
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert len(conn.execute(select(split)).fetchall()) == 2


def test_ambiguous_garmin_day_is_not_merged(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")
        _garmin_boulder(conn, "g2", "2026-05-14")
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-05-14T20:00:00.000Z")],
        )
        _load(
            conn,
            tmp_path,
            KIND_ASCENTS,
            "ascentsForUser",
            [_ascent("a1", "s1", "2026-05-14T20:01:00.000Z", "v1")],
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        acts = conn.execute(select(activity.c.primary_source)).fetchall()
        assert sorted(a.primary_source for a in acts) == [
            "garmin_connect",
            "garmin_connect",
            "kaya",
        ]
        # Garmin's own splits untouched.
        assert len(conn.execute(select(split).where(split.c.activity_id == "g1")).fetchall()) == 1


def test_session_without_routes_never_wipes_garmin_data(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-05-14T20:00:00.000Z")],
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert len(conn.execute(select(activity)).fetchall()) == 1
        assert conn.execute(select(split.c.climb_grade)).scalar_one() == 9


def test_kaya_only_activity_is_removed_when_a_garmin_match_appears(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-05-14T20:00:00.000Z")],
        )
        _load(
            conn,
            tmp_path,
            KIND_ASCENTS,
            "ascentsForUser",
            [_ascent("a1", "s1", "2026-05-14T20:01:00.000Z", "v1")],
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert conn.execute(select(activity.c.primary_source)).scalar_one() == "kaya"
        _garmin_boulder(conn, "g1", "2026-05-14")
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert conn.execute(select(activity.c.primary_source)).scalar_one() == "garmin_connect"


def _routes(conn: Connection) -> list[tuple[int | None, str | None]]:
    rows = conn.execute(select(split).order_by(split.c.split_index)).fetchall()
    return [(r.climb_grade, r.climb_result) for r in rows]


def test_kaya_only_activity_includes_attempts(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-09-27T03:00:00.000Z", attempted=["v4", "v?"])],
        )
        _load(
            conn,
            tmp_path,
            KIND_ASCENTS,
            "ascentsForUser",
            [_ascent("a1", "s1", "2026-09-27T03:05:00.000Z", "v2")],
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert _routes(conn) == [(2, "completed"), (4, "attempt"), (None, "attempt")]


def test_garmin_and_kaya_attempts_are_combined_not_doubled(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")  # one Garmin attempt row (grade 9)
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-05-14T20:00:00.000Z", attempted=["v4", "v5", "v6"])],
        )
        _load(
            conn,
            tmp_path,
            KIND_ASCENTS,
            "ascentsForUser",
            [_ascent("a1", "s1", "2026-05-14T20:01:00.000Z", "v1")],
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        # Garmin's 1 attempt kept; Kaya's 3 attempts add only the 2 beyond it.
        assert sorted(_routes(conn), key=str) == sorted(
            [(9, "attempt"), (1, "completed"), (5, "attempt"), (6, "attempt")], key=str
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert len(_routes(conn)) == 4  # idempotent


def test_garmin_with_more_attempts_than_kaya_keeps_all_garmin(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-05-14T20:00:00.000Z", attempted=["v4"])],
        )
        _load(
            conn,
            tmp_path,
            KIND_ASCENTS,
            "ascentsForUser",
            [_ascent("a1", "s1", "2026-05-14T20:01:00.000Z", "v1")],
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert sorted(_routes(conn), key=str) == sorted([(9, "attempt"), (1, "completed")], key=str)


def test_route_label_prefers_kaya_name_else_colour_and_wall() -> None:
    assert route_label("Dragon", "Pink", "A8") == "Dragon"
    assert route_label(None, "Pink", "A8 - Alcove, Right") == "Pink - A8 - Alcove, Right"
    assert route_label(None, None, "A8") == "A8"
    assert route_label(None, None, None) is None


def test_split_carries_the_kaya_route_label(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-09-27T03:00:00.000Z")],
        )
        ascent = _ascent("a1", "s1", "2026-09-27T03:05:00.000Z", "v2")
        ascent["climb"]["color"] = {"id": "1", "name": "Pink"}
        ascent["climb"]["wall"] = {"id": "2", "name": "A8 - Alcove, Right"}
        _load(conn, tmp_path, KIND_ASCENTS, "ascentsForUser", [ascent])
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert conn.execute(select(split.c.climb_name)).scalar_one() == "Pink - A8 - Alcove, Right"


def test_attempt_counts_expand_into_attempt_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-09-27T03:00:00.000Z", attempted=["v4"])],
        )
        send = _ascent("a1", "s1", "2026-09-27T03:05:00.000Z", "v4", "Redpoint")
        send["attempts"] = 4  # 4 tries including the send -> 3 failed + 1 send
        _load(conn, tmp_path, KIND_ASCENTS, "ascentsForUser", [send])
        # Unsent climb "s1_0" is climb id "0" -> Kaya says 5 lifetime attempts.
        _load(conn, tmp_path, KIND_UNSENT, "attemptedClimbsForUser", [{"id": "0", "attempts": 5}])
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        routes = _routes(conn)
        assert routes.count((4, "completed")) == 1
        assert routes.count((4, "attempt")) == 3 + 5


def test_attempts_come_before_their_send_and_unsent_climbs_last(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-09-27T03:00:00.000Z", attempted=["v5"])],
        )
        first = _ascent("a1", "s1", "2026-09-27T03:05:00.000Z", "v2", "Redpoint")
        first["attempts"] = 3  # 2 failed tries, then the send
        second = _ascent("a2", "s1", "2026-09-27T03:10:00.000Z", "v3")
        _load(conn, tmp_path, KIND_ASCENTS, "ascentsForUser", [second, first])
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert _routes(conn) == [
            (2, "attempt"),
            (2, "attempt"),
            (2, "completed"),
            (3, "completed"),
            (5, "attempt"),
        ]


def test_rows_of_the_same_climb_are_grouped_even_with_two_sends(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-09-27T03:00:00.000Z")],
        )
        a = _ascent("a1", "s1", "2026-09-27T03:05:00.000Z", "v3", "Flash")
        b = _ascent("a2", "s1", "2026-09-27T03:10:00.000Z", "v1")
        c = _ascent("a3", "s1", "2026-09-27T04:00:00.000Z", "v3", "Repeat")  # same climb as a1
        c["climb"]["id"] = a["climb"]["id"]
        _load(conn, tmp_path, KIND_ASCENTS, "ascentsForUser", [c, b, a])
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        # The v3 climb's two sends sit together, ahead of the later-started v1 climb.
        assert _routes(conn) == [(3, "completed"), (3, "completed"), (1, "completed")]
