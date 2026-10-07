"""Kaya ingest: parse/store idempotency, grade mapping, and the local-date merge with Garmin
bouldering activities."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
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
        # The split remembers Kaya's id for the route -- what a note on it is keyed by.
        assert conn.execute(select(split.c.climb_kaya_id)).scalar_one() == "ca1"


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


def _kaya_day(conn: Connection, tmp_path: Path, attempted: list[str]) -> None:
    _load(
        conn,
        tmp_path,
        KIND_SESSIONS,
        "sessionsForUser",
        [_session("s1", "2026-05-14T20:00:00.000Z", attempted=attempted)],
    )
    _load(
        conn,
        tmp_path,
        KIND_ASCENTS,
        "ascentsForUser",
        [_ascent("a1", "s1", "2026-05-14T20:01:00.000Z", "v1")],
    )


def test_kaya_supplies_the_routes_and_garmin_rows_are_kept_for_time(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")  # one Garmin effort: V9 attempt, 100 s
        conn.execute(split.update().values(duration_s=100.0, climb_avg_hr=95.0))
        _kaya_day(conn, tmp_path, attempted=["v4", "v5"])
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        assert len(conn.execute(select(activity)).fetchall()) == 1  # merged, no second activity
        routes = conn.execute(
            select(split.c.climb_grade, split.c.climb_result, split.c.source).where(
                split.c.split_type == "climb_active"
            )
        ).fetchall()
        # Kaya's send and two attempts, plus Garmin's V9 effort that Kaya never logged.
        assert sorted((r.climb_grade, r.climb_result, r.source) for r in routes) == [
            (1, "completed", "kaya"),
            (4, "attempt", "kaya"),
            (5, "attempt", "kaya"),
            (9, "attempt", "garmin_extra"),
        ]
        extra = conn.execute(select(split).where(split.c.source == "garmin_extra")).one()
        assert (extra.duration_s, extra.climb_avg_hr) == (100.0, 95.0)  # keeps Garmin timing


def test_garmin_efforts_kaya_already_has_are_not_duplicated(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")  # Garmin effort: V9 attempt
        conn.execute(
            split.update().values(climb_grade=1, climb_result="completed", duration_s=50.0)
        )
        _kaya_day(conn, tmp_path, attempted=[])  # Kaya: one V1 send
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        routes = conn.execute(
            select(split.c.climb_grade, split.c.source).where(split.c.split_type == "climb_active")
        ).fetchall()
        assert [(r.climb_grade, r.source) for r in routes] == [(1, "kaya")]
        # The matched Garmin row is kept (demoted) so its duration still counts toward climb time.
        old = conn.execute(
            select(split).where(split.c.split_type == "climb_active_superseded")
        ).one()
        assert (old.duration_s, old.garmin_grade, old.garmin_result) == (50.0, 1, "completed")


def test_ungraded_kaya_routes_absorb_leftover_garmin_efforts(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")  # Garmin effort: V9 attempt
        _load(
            conn,
            tmp_path,
            KIND_SESSIONS,
            "sessionsForUser",
            [_session("s1", "2026-05-14T20:00:00.000Z", attempted=["v?"])],
        )
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        # Kaya's one ungraded attempt stands in for Garmin's one attempt: no extra is added.
        routes = conn.execute(
            select(split.c.climb_grade, split.c.source).where(split.c.split_type == "climb_active")
        ).fetchall()
        assert [(r.climb_grade, r.source) for r in routes] == [(None, "kaya")]


def test_reapplying_a_merged_day_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _garmin_boulder(conn, "g1", "2026-05-14")
        _kaya_day(conn, tmp_path, attempted=["v4"])
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        first = conn.execute(select(split.c.split_type, split.c.climb_grade)).fetchall()
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        apply_kaya_sessions(conn, athlete_id=DEFAULT_ATHLETE_ID)
        again = conn.execute(select(split.c.split_type, split.c.climb_grade)).fetchall()
        assert sorted(map(tuple, first), key=str) == sorted(map(tuple, again), key=str)
        # Kaya's send and attempt, plus Garmin's unlogged V9 effort -- stable across re-runs.
        assert [t for t, _ in again].count("climb_active") == 3


def test_import_records_an_ingest_run_on_success_and_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from perseverer.adapters import kaya_ingest
    from perseverer.adapters.kaya import KayaAuthRequired
    from perseverer.db.schema import ingest_run

    sessions = json.dumps(
        {"data": {"sessionsForUser": [_session("s1", "2026-09-27T03:00:00.000Z")]}}
    ).encode()
    ascents = json.dumps(
        {"data": {"ascentsForUser": [_ascent("a1", "s1", "2026-09-27T03:05:00.000Z", "v2")]}}
    ).encode()
    monkeypatch.setattr(
        kaya_ingest,
        "fetch_pages",
        lambda _dir: [(KIND_SESSIONS, sessions), (KIND_ASCENTS, ascents)],
    )
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        summary = kaya_ingest.import_kaya(
            conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID, tokenstore_dir=tmp_path
        )
        assert (summary.sessions, summary.ascents, summary.touched_dates) == (1, 1, 1)
        run = conn.execute(select(ingest_run)).one()
        assert (run.source, run.status, run.items_seen, run.items_new) == ("kaya", "success", 2, 1)
        assert run.finished_at is not None

        def boom(_dir: Path) -> list[tuple[str, bytes]]:
            raise KayaAuthRequired("Kaya session expired")

        monkeypatch.setattr(kaya_ingest, "fetch_pages", boom)
        with pytest.raises(KayaAuthRequired):
            kaya_ingest.import_kaya(
                conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID, tokenstore_dir=tmp_path
            )
        runs = conn.execute(select(ingest_run).order_by(ingest_run.c.id)).fetchall()
        assert [r.status for r in runs] == ["success", "failed"]
        assert "Kaya session expired" in runs[1].errors
