from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from perseverer.db.schema import athlete_default_shoe
from tests.api.conftest import seed_activity


def _create_shoe(
    client: TestClient, auth_headers: dict[str, str], *, max_distance_km: float | None = 800
) -> dict[str, object]:
    response = client.post(
        "/api/v1/gear/shoes",
        headers=auth_headers,
        json={
            "brand": "Brooks",
            "model": "Catamount",
            "size": "10.5",
            "comments": "Trail pair",
            "initial_distance_km": 24.7,
            "max_distance_km": max_distance_km,
        },
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def test_retire_hides_pair_and_removes_sport_defaults(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    created = _create_shoe(client, auth_headers)
    shoe_id = created["id"]
    default_response = client.put(
        "/api/v1/gear/defaults/running",
        headers=auth_headers,
        json={"shoe_id": shoe_id},
    )
    assert default_response.status_code == 200

    retired = client.put(
        f"/api/v1/gear/shoes/{shoe_id}/retire", headers=auth_headers, json={}
    )
    assert retired.status_code == 200
    assert retired.json()["retired"] is True
    assert retired.json()["default_sports"] == []

    active = client.get("/api/v1/gear/shoes", headers=auth_headers)
    assert active.status_code == 200
    assert active.json() == []

    all_shoes = client.get(
        "/api/v1/gear/shoes?include_retired=true", headers=auth_headers
    )
    assert all_shoes.status_code == 200
    assert [pair["id"] for pair in all_shoes.json()] == [shoe_id]

    with engine.connect() as conn:
        mapping_count = conn.execute(
            select(func.count()).select_from(athlete_default_shoe)
        ).scalar_one()
    assert mapping_count == 0


def test_shoe_can_have_no_mileage_limit(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create_shoe(client, auth_headers, max_distance_km=None)
    assert created["max_distance_km"] is None
    assert created["remaining_km"] is None
    assert created["over_limit"] is False
    assert client.get("/api/v1/gear/alerts", headers=auth_headers).json() == []


def test_replacing_an_activity_pair_recalculates_both_shoe_mileages(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    first_shoe = _create_shoe(client, auth_headers)
    second_shoe = _create_shoe(client, auth_headers)
    with engine.connect() as conn:
        seed_activity(conn, activity_id="run-1", distance_m=5000)

    assigned = client.put(
        "/api/v1/gear/activities/run-1/shoe",
        headers=auth_headers,
        json={"shoe_id": first_shoe["id"]},
    )
    assert assigned.status_code == 200

    replaced = client.put(
        "/api/v1/gear/activities/run-1/shoe",
        headers=auth_headers,
        json={"shoe_id": second_shoe["id"]},
    )
    assert replaced.status_code == 200
    assert replaced.json() == {"shoe_id": second_shoe["id"]}

    pairs = client.get("/api/v1/gear/shoes", headers=auth_headers)
    assert pairs.status_code == 200
    mileage_by_id = {pair["id"]: pair["distance_km"] for pair in pairs.json()}
    assert mileage_by_id[first_shoe["id"]] == 24.7
    assert mileage_by_id[second_shoe["id"]] == 29.7
