"""GET /activities search filters used by the run comparison: name text, distance range, ids."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import activity
from tests.api.conftest import seed_activity


def _seed(engine: Engine) -> None:
    with engine.connect() as conn:
        for activity_id, name, distance in [
            ("easy", "Easy run by the river", 8000.0),
            ("tempo", "Tempo 3x10'", 12000.0),
            ("long", "Long Run", 21100.0),
            ("odd", "100% effort_test", 5000.0),
        ]:
            seed_activity(conn, activity_id=activity_id, distance_m=distance)
            conn.execute(activity.update().where(activity.c.id == activity_id).values(name=name))
        conn.commit()


def _ids(client: TestClient, headers: dict[str, str], query: str) -> tuple[set[str], int]:
    r = client.get(f"/api/v1/activities?{query}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    return {a["id"] for a in body["items"]}, body["total"]


def test_name_search_is_case_insensitive_and_literal(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed(engine)
    assert _ids(client, auth_headers, "q=RUN") == ({"easy", "long"}, 2)
    # "%" and "_" are matched as typed, not as wildcards.
    assert _ids(client, auth_headers, "q=100%25") == ({"odd"}, 1)
    assert _ids(client, auth_headers, "q=lon_") == (set(), 0)  # "_" would match "Long"


def test_distance_range_and_ids(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed(engine)
    assert _ids(client, auth_headers, "min_distance_m=7000&max_distance_m=13000") == (
        {"easy", "tempo"},
        2,
    )
    assert _ids(client, auth_headers, "ids=long&ids=odd") == ({"long", "odd"}, 2)
    assert _ids(client, auth_headers, "ids=long&q=tempo") == (set(), 0)
