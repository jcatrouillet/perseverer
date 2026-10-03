"""PUT /kaya-climbs/{id}/note -- the athlete's note on a Kaya route, which follows the route (by its
Kaya id) into every activity it appears in, as `SplitOut.note` on each of its rows."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.db.schema import activity, kaya_climb_note, split
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def _seed_session(engine: Engine, activity_id: str, local_date: str, climb_ids: list[str]) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date=local_date,
                sport="rock_climbing",
                sub_sport="bouldering",
                primary_source="kaya",
                created_at=now,
                updated_at=now,
            )
        )
        for i, climb_id in enumerate(climb_ids):
            conn.execute(
                split.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id=activity_id,
                    split_index=i,
                    split_type="climb_active",
                    climb_grade=2,
                    climb_result="completed",
                    climb_name=f"Route {climb_id}",
                    source="kaya",
                    climb_kaya_id=climb_id,
                )
            )
        conn.commit()


def _splits(
    client: TestClient, headers: dict[str, str], activity_id: str
) -> list[dict[str, object]]:
    r = client.get(f"/api/v1/activities/{activity_id}", headers=headers)
    assert r.status_code == 200, r.text
    return list(r.json()["splits"])


def test_a_note_follows_the_route_into_every_activity(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_session(engine, "act-a", "2026-10-01", ["111", "222"])
    _seed_session(engine, "act-b", "2026-10-08", ["111"])

    assert all(s["note"] is None for s in _splits(client, auth_headers, "act-a"))

    r = client.put(
        "/api/v1/kaya-climbs/111/note",
        json={"note": "  heel hook at the top  "},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"climb_kaya_id": "111", "note": "heel hook at the top"}

    notes_a = {s["climb_kaya_id"]: s["note"] for s in _splits(client, auth_headers, "act-a")}
    assert notes_a == {"111": "heel hook at the top", "222": None}
    # The same route, repeated in a later session, carries the same note.
    assert _splits(client, auth_headers, "act-b")[0]["note"] == "heel hook at the top"


def test_editing_and_clearing_a_note(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_session(engine, "act-a", "2026-10-01", ["111"])
    url = "/api/v1/kaya-climbs/111/note"
    client.put(url, json={"note": "first"}, headers=auth_headers)
    client.put(url, json={"note": "second"}, headers=auth_headers)  # replaces, never duplicates
    with engine.connect() as conn:
        rows = conn.execute(select(kaya_climb_note.c.note)).fetchall()
    assert [r.note for r in rows] == ["second"]

    cleared = client.put(url, json={"note": "   "}, headers=auth_headers)
    assert cleared.json() == {"climb_kaya_id": "111", "note": None}
    assert _splits(client, auth_headers, "act-a")[0]["note"] is None
    with engine.connect() as conn:
        assert conn.execute(select(kaya_climb_note.c.id)).fetchall() == []


def test_unknown_route_too_long_and_unauthenticated(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_session(engine, "act-a", "2026-10-01", ["111"])
    assert (
        client.put(
            "/api/v1/kaya-climbs/999/note", json={"note": "x"}, headers=auth_headers
        ).status_code
        == 404
    )
    too_long = client.put(
        "/api/v1/kaya-climbs/111/note", json={"note": "x" * 2001}, headers=auth_headers
    )
    assert too_long.status_code == 422
    assert client.put("/api/v1/kaya-climbs/111/note", json={"note": "x"}).status_code in (401, 503)
