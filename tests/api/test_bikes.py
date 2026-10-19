"""Bikes and tires: ledgers, defaults per category, tire swaps, retirement, alerts, rebuild."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.bikes import Ride, resolve_bike, ride_categories
from perseverer.db.schema import activity
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity

Json = dict[str, Any]


def _bike(client: TestClient, headers: dict[str, str], **overrides: object) -> Json:
    body: Json = {"brand": "Canyon", "model": "Endurace", "year": 2023, "size": "M"}
    body.update(overrides)
    r = client.post("/api/v1/gear/bikes", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return cast(Json, r.json())


def _tire(client: TestClient, headers: dict[str, str], **overrides: object) -> Json:
    body: Json = {
        "brand": "Continental",
        "model": "GP5000",
        "size": "700x28",
        "initial_distance_km": 0,
        "max_distance_km": 5000,
    }
    body.update(overrides)
    r = client.post("/api/v1/gear/tires", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return cast(Json, r.json())


def _ride(
    engine: Engine,
    activity_id: str,
    day: str,
    km: float,
    *,
    sport: str = "cycling",
    sub_sport: str | None = None,
    commute: bool = False,
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id=activity_id,
            local_date=day,
            sport=sport,
            sub_sport=sub_sport,
            distance_m=km * 1000,
        )
        conn.execute(
            activity.update()
            .where(activity.c.id == activity_id)
            .values(start_time_utc=dt.datetime.fromisoformat(f"{day}T08:00:00"), is_commute=commute)
        )
        conn.commit()


def _default(client: TestClient, headers: dict[str, str], category: str, bike_id: str) -> Json:
    r = client.put(
        f"/api/v1/gear/bike-defaults/{category}", headers=headers, json={"bike_id": bike_id}
    )
    assert r.status_code == 200, r.text
    return cast(Json, r.json())


def _bike_by_id(client: TestClient, headers: dict[str, str], bike_id: str) -> Json:
    bikes = client.get("/api/v1/gear/bikes?include_retired=true", headers=headers).json()
    return cast(Json, next(b for b in bikes if b["id"] == bike_id))


def _tire_by_id(client: TestClient, headers: dict[str, str], tire_id: str) -> Json:
    tires = client.get("/api/v1/gear/tires?include_retired=true", headers=headers).json()
    return cast(Json, next(t for t in tires if t["id"] == tire_id))


def _install(
    client: TestClient,
    headers: dict[str, str],
    tire_id: str,
    bike_id: str,
    position: str,
    installed_on: str | None = None,
) -> Json:
    body: Json = {"bike_id": bike_id, "position": position}
    if installed_on:
        body["installed_on"] = installed_on
    r = client.put(f"/api/v1/gear/tires/{tire_id}/install", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return cast(Json, r.json())


class TestCategories:
    def _ride(self, **kw: Any) -> Ride:
        base: Json = {
            "id": "r",
            "sport": "cycling",
            "sub_sport": None,
            "is_commute": False,
            "bike_id": None,
            "start_time_utc": dt.datetime(2026, 1, 1),
            "distance_m": 1000.0,
        }
        base.update(kw)
        return Ride(**base)

    def test_most_specific_category_first_and_general_last(self) -> None:
        assert ride_categories(self._ride()) == ["cycling"]
        assert ride_categories(self._ride(sub_sport="road")) == ["road", "cycling"]
        assert ride_categories(self._ride(sport="mountain_biking")) == ["mountain", "cycling"]
        assert ride_categories(self._ride(sport="gravel_cycling")) == ["gravel", "cycling"]
        assert ride_categories(self._ride(sport="e_bike_fitness")) == ["ebike", "cycling"]
        assert ride_categories(self._ride(sub_sport="e_bike_mountain")) == ["emtb", "cycling"]
        # The commute tag outranks the rest.
        assert ride_categories(self._ride(sub_sport="road", is_commute=True)) == [
            "commute",
            "road",
            "cycling",
        ]

    def test_default_applies_only_from_its_own_assignment_time(self) -> None:
        ride = self._ride(start_time_utc=dt.datetime(2026, 3, 1))
        before = {"cycling": ("b1", dt.datetime(2026, 4, 1))}
        after = {"cycling": ("b1", dt.datetime(2026, 2, 1))}
        assert resolve_bike(ride, before) == (None, False)
        assert resolve_bike(ride, after) == ("b1", True)

    def test_specific_default_beats_general_and_explicit_beats_both(self) -> None:
        defaults = {
            "cycling": ("general", dt.datetime(2020, 1, 1)),
            "commute": ("commuter", dt.datetime(2020, 1, 1)),
        }
        assert resolve_bike(self._ride(), defaults) == ("general", True)
        assert resolve_bike(self._ride(is_commute=True), defaults) == ("commuter", True)
        assert resolve_bike(self._ride(is_commute=True, bike_id="mine"), defaults) == (
            "mine",
            False,
        )


class TestBikes:
    def test_a_bike_without_tires_is_fine_and_counts_its_initial_distance(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        bike = _bike(client, auth_headers, initial_distance_km=1200)
        assert bike["distance_km"] == 1200
        assert bike["front_tire"] is None and bike["rear_tire"] is None
        assert bike["default_categories"] == []
        assert bike["year"] == 2023 and bike["size"] == "M"

    def test_default_bike_collects_rides_from_its_assignment(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        _ride(engine, "old", "2020-01-01", 40)  # before the default existed: not charged
        bike = _bike(client, auth_headers, initial_distance_km=100)
        _default(client, auth_headers, "cycling", bike["id"])
        _ride(engine, "new1", "2999-01-01", 30)
        _ride(engine, "new2", "2999-01-02", 20)
        got = _bike_by_id(client, auth_headers, bike["id"])
        assert got["distance_km"] == 150
        assert got["default_categories"] == ["cycling"]

    def test_a_default_can_cover_several_categories_and_the_specific_one_wins(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        general = _bike(client, auth_headers, brand="Trek", model="Domane")
        commuter = _bike(client, auth_headers, brand="Specialized", model="Sirrus")
        _default(client, auth_headers, "cycling", general["id"])
        _default(client, auth_headers, "commute", commuter["id"])
        _default(client, auth_headers, "road", commuter["id"])
        _ride(engine, "a", "2999-02-01", 10)
        _ride(engine, "b", "2999-02-02", 25, commute=True)
        _ride(engine, "c", "2999-02-03", 5, sub_sport="road")
        assert _bike_by_id(client, auth_headers, general["id"])["distance_km"] == 10
        commuter_out = _bike_by_id(client, auth_headers, commuter["id"])
        assert commuter_out["distance_km"] == 30
        assert commuter_out["default_categories"] == ["commute", "road"]

    def test_an_explicit_bike_per_ride_beats_the_default_and_can_be_cleared(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        default = _bike(client, auth_headers, brand="A", model="default")
        other = _bike(client, auth_headers, brand="B", model="other")
        _default(client, auth_headers, "cycling", default["id"])
        _ride(engine, "r1", "2999-03-01", 50)

        r = client.get("/api/v1/gear/activities/r1/bike", headers=auth_headers)
        assert r.json() == {"bike_id": default["id"], "is_default": True}

        r = client.put(
            "/api/v1/gear/activities/r1/bike", headers=auth_headers, json={"bike_id": other["id"]}
        )
        assert r.json() == {"bike_id": other["id"], "is_default": False}
        assert _bike_by_id(client, auth_headers, other["id"])["distance_km"] == 50
        assert _bike_by_id(client, auth_headers, default["id"])["distance_km"] == 0

        r = client.put(
            "/api/v1/gear/activities/r1/bike", headers=auth_headers, json={"bike_id": None}
        )
        assert r.json() == {"bike_id": default["id"], "is_default": True}

    def test_only_rides_can_have_a_bike_and_the_bike_must_exist(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="run", sport="running")
        _ride(engine, "ride", "2999-04-01", 10)
        bike = _bike(client, auth_headers)
        assert (
            client.put(
                "/api/v1/gear/activities/run/bike",
                headers=auth_headers,
                json={"bike_id": bike["id"]},
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/v1/gear/activities/ride/bike", headers=auth_headers, json={"bike_id": "nope"}
            ).status_code
            == 404
        )
        assert (
            client.get("/api/v1/gear/activities/missing/bike", headers=auth_headers).status_code
            == 404
        )

    def test_the_explicit_choice_survives_a_rebuild(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        from perseverer.sport_override import apply_sport_overrides

        bike = _bike(client, auth_headers)
        _ride(engine, "r1", "2999-05-01", 20)
        client.put(
            "/api/v1/gear/activities/r1/bike", headers=auth_headers, json={"bike_id": bike["id"]}
        )
        with engine.connect() as conn:
            # "Rebuild": the derived row comes back without the choice.
            conn.execute(activity.update().where(activity.c.id == "r1").values(bike_id=None))
            assert apply_sport_overrides(conn, athlete_id=DEFAULT_ATHLETE_ID) == 1
            assert conn.execute(select(activity.c.bike_id)).scalar_one() == bike["id"]

    def test_retiring_a_bike_clears_its_defaults_and_takes_its_tires_off(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        bike = _bike(client, auth_headers)
        _default(client, auth_headers, "road", bike["id"])
        tire = _tire(client, auth_headers, bike_id=bike["id"], position="front")
        assert _bike_by_id(client, auth_headers, bike["id"])["front_tire"]["id"] == tire["id"]

        r = client.put(f"/api/v1/gear/bikes/{bike['id']}/retire", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["retired"] is True
        assert r.json()["default_categories"] == []
        assert r.json()["front_tire"] is None
        assert client.get("/api/v1/gear/bikes", headers=auth_headers).json() == []
        # The tire is in stock, not retired.
        stock = _tire_by_id(client, auth_headers, tire["id"])
        assert stock["bike_id"] is None and stock["retired"] is False
        # A retired bike can't be a default or take a tire.
        assert (
            client.put(
                "/api/v1/gear/bike-defaults/cycling",
                headers=auth_headers,
                json={"bike_id": bike["id"]},
            ).status_code
            == 404
        )

    def test_default_category_is_validated_and_can_be_cleared(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        bike = _bike(client, auth_headers)
        bad = client.put(
            "/api/v1/gear/bike-defaults/unicycle",
            headers=auth_headers,
            json={"bike_id": bike["id"]},
        )
        assert bad.status_code == 422
        _default(client, auth_headers, "gravel", bike["id"])
        assert (
            client.delete("/api/v1/gear/bike-defaults/gravel", headers=auth_headers).status_code
            == 200
        )
        assert (
            client.delete("/api/v1/gear/bike-defaults/gravel", headers=auth_headers).status_code
            == 404
        )
        assert _bike_by_id(client, auth_headers, bike["id"])["default_categories"] == []


class TestTires:
    def test_front_and_rear_tires_each_inherit_the_rides_while_installed(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        bike = _bike(client, auth_headers)
        _default(client, auth_headers, "cycling", bike["id"])
        front = _tire(
            client, auth_headers, bike_id=bike["id"], position="front", initial_distance_km=10
        )
        rear = _tire(
            client, auth_headers, bike_id=bike["id"], position="rear", max_distance_km=3000
        )
        _ride(engine, "r1", "2999-06-01", 40)
        _ride(engine, "r2", "2999-06-02", 60)

        assert _tire_by_id(client, auth_headers, front["id"])["distance_km"] == 110
        rear_out = _tire_by_id(client, auth_headers, rear["id"])
        assert rear_out["distance_km"] == 100
        assert rear_out["remaining_km"] == 2900
        assert rear_out["position"] == "rear" and rear_out["bike_name"] == "Canyon Endurace"
        got = _bike_by_id(client, auth_headers, bike["id"])
        assert got["front_tire"]["id"] == front["id"] and got["rear_tire"]["id"] == rear["id"]

    def test_replacing_a_tire_stops_the_old_one_and_starts_the_new_one(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        bike = _bike(client, auth_headers)
        _default(client, auth_headers, "cycling", bike["id"])
        old = _tire(client, auth_headers, bike_id=bike["id"], position="rear", model="Old")
        _ride(engine, "before", "2999-07-01", 100)
        # The new tire goes on from a later day: the earlier ride stays on the old tire.
        new = _tire(client, auth_headers, model="New")
        _install(client, auth_headers, new["id"], bike["id"], "rear", "2999-07-10")
        _ride(engine, "after", "2999-07-11", 30)

        assert _tire_by_id(client, auth_headers, old["id"])["distance_km"] == 100
        assert _tire_by_id(client, auth_headers, new["id"])["distance_km"] == 30
        old_out = _tire_by_id(client, auth_headers, old["id"])
        assert old_out["bike_id"] is None  # in stock
        assert _bike_by_id(client, auth_headers, bike["id"])["rear_tire"]["id"] == new["id"]

    def test_a_tire_moved_between_bikes_follows_the_rides_of_each(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        road = _bike(client, auth_headers, brand="Road", model="One")
        gravel = _bike(client, auth_headers, brand="Gravel", model="Two")
        _default(client, auth_headers, "road", road["id"])
        _default(client, auth_headers, "gravel", gravel["id"])
        tire = _tire(client, auth_headers, bike_id=road["id"], position="front")
        _ride(engine, "r1", "2999-08-01", 20, sub_sport="road")
        _install(client, auth_headers, tire["id"], gravel["id"], "front", "2999-08-05")
        _ride(engine, "r2", "2999-08-06", 35, sport="gravel_cycling")
        _ride(engine, "r3", "2999-08-07", 15, sub_sport="road")  # on the road bike, tire is away

        assert _tire_by_id(client, auth_headers, tire["id"])["distance_km"] == 55
        assert _bike_by_id(client, auth_headers, road["id"])["front_tire"] is None
        assert _bike_by_id(client, auth_headers, gravel["id"])["front_tire"]["id"] == tire["id"]

    def test_a_tire_keeps_its_comment(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        tire = _tire(client, auth_headers, comments="  Tubeless, run at 70 psi  ")
        assert tire["comments"] == "Tubeless, run at 70 psi"
        assert (
            _tire_by_id(client, auth_headers, tire["id"])["comments"] == "Tubeless, run at 70 psi"
        )
        assert _tire(client, auth_headers)["comments"] is None

    def test_removing_keeps_the_tire_and_retiring_ends_it(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        bike = _bike(client, auth_headers)
        tire = _tire(client, auth_headers, bike_id=bike["id"], position="front")

        removed = client.put(f"/api/v1/gear/tires/{tire['id']}/remove", headers=auth_headers)
        assert removed.status_code == 200
        assert removed.json()["bike_id"] is None and removed.json()["retired"] is False
        assert _bike_by_id(client, auth_headers, bike["id"])["front_tire"] is None
        _install(client, auth_headers, tire["id"], bike["id"], "front")

        retired = client.put(f"/api/v1/gear/tires/{tire['id']}/retire", headers=auth_headers)
        assert retired.json()["retired"] is True and retired.json()["bike_id"] is None
        assert client.get("/api/v1/gear/tires", headers=auth_headers).json() == []
        # A retired tire can't be fitted again.
        again = client.put(
            f"/api/v1/gear/tires/{tire['id']}/install",
            headers=auth_headers,
            json={"bike_id": bike["id"], "position": "front"},
        )
        assert again.status_code == 404

    def test_tire_validation(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        bike = _bike(client, auth_headers)
        base: Json = {"brand": "X", "model": "Y"}
        # A bike without a position (or the reverse) is refused.
        for extra in ({"bike_id": bike["id"]}, {"position": "front"}):
            r = client.post("/api/v1/gear/tires", headers=auth_headers, json={**base, **extra})
            assert r.status_code == 422
        bad_pos = {**base, "bike_id": bike["id"], "position": "middle"}
        assert (
            client.post("/api/v1/gear/tires", headers=auth_headers, json=bad_pos).status_code == 422
        )
        missing = {**base, "bike_id": "nope", "position": "front"}
        assert (
            client.post("/api/v1/gear/tires", headers=auth_headers, json=missing).status_code == 404
        )
        # Back-dating before the tire it would replace was fitted is refused.
        tire = _tire(client, auth_headers, bike_id=bike["id"], position="front")
        other = _tire(client, auth_headers, model="Other")
        r = client.put(
            f"/api/v1/gear/tires/{other['id']}/install",
            headers=auth_headers,
            json={"bike_id": bike["id"], "position": "front", "installed_on": "2000-01-01"},
        )
        assert r.status_code == 422
        assert _bike_by_id(client, auth_headers, bike["id"])["front_tire"]["id"] == tire["id"]

    def test_unknown_tire_is_404(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        for path in ("install", "remove", "retire"):
            r = client.put(
                f"/api/v1/gear/tires/nope/{path}",
                headers=auth_headers,
                json={"bike_id": "x", "position": "front"},
            )
            assert r.status_code == 404


class TestAlerts:
    def test_an_installed_tire_at_its_limit_raises_an_alert_and_a_stock_one_does_not(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        bike = _bike(client, auth_headers)
        _default(client, auth_headers, "cycling", bike["id"])
        worn = _tire(
            client,
            auth_headers,
            bike_id=bike["id"],
            position="rear",
            initial_distance_km=900,
            max_distance_km=1000,
        )
        _tire(client, auth_headers, model="Spare", initial_distance_km=5000, max_distance_km=100)
        assert client.get("/api/v1/gear/alerts", headers=auth_headers).json() == []
        _ride(engine, "r1", "2999-09-01", 100)

        alerts = client.get("/api/v1/gear/alerts", headers=auth_headers).json()
        assert [a["kind"] for a in alerts] == ["tire"]
        assert alerts[0]["tire_id"] == worn["id"]
        assert alerts[0]["position"] == "rear"
        assert alerts[0]["bike_name"] == "Canyon Endurace"
        assert alerts[0]["distance_km"] == 1000 and alerts[0]["max_distance_km"] == 1000
        assert alerts[0]["shoe_id"] is None
        assert _tire_by_id(client, auth_headers, worn["id"])["over_limit"] is True

    def test_each_over_limit_tire_is_emailed_once(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        from unittest.mock import MagicMock, patch

        from perseverer.bikes import send_tire_alerts

        bike = _bike(client, auth_headers)
        _tire(
            client,
            auth_headers,
            bike_id=bike["id"],
            position="front",
            initial_distance_km=500,
            max_distance_km=400,
        )
        settings = MagicMock(smtp_configured=True)
        with patch("perseverer.bikes.send_email") as send, engine.connect() as conn:
            assert send_tire_alerts(conn, settings, DEFAULT_ATHLETE_ID, "me@example.com") == 1
            assert send_tire_alerts(conn, settings, DEFAULT_ATHLETE_ID, "me@example.com") == 0
        assert send.call_count == 1
        assert "front tire" in send.call_args.kwargs["subject"]


def test_endpoints_require_auth(client: TestClient) -> None:
    for method, path in (
        ("get", "/api/v1/gear/bikes"),
        ("post", "/api/v1/gear/bikes"),
        ("get", "/api/v1/gear/tires"),
        ("put", "/api/v1/gear/bike-defaults/cycling"),
        ("put", "/api/v1/gear/activities/x/bike"),
    ):
        assert getattr(client, method)(path).status_code in (401, 403)
