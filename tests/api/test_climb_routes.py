"""Tests for the bouldering route-editing endpoints (PATCH/POST/DELETE .../climb-routes) and the
two read endpoints built on the same data (GET .../climb-comparisons, GET .../climbing-summary).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import split
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity


def _add_split(
    engine: Engine,
    *,
    activity_id: str,
    split_index: int,
    split_type: str = "climb_active",
    grade: int | None = 2,
    result: str | None = "attempt",
    duration_s: float | None = 60.0,
) -> None:
    with engine.connect() as conn:
        conn.execute(
            split.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                split_index=split_index,
                split_type=split_type,
                climb_grade=grade,
                climb_result=result,
                duration_s=duration_s,
            )
        )
        conn.commit()


class TestPatchClimbRouteStatus:
    def test_overrides_and_returns_the_updated_split(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")
        _add_split(engine, activity_id="a1", split_index=0, result="attempt")

        r = client.patch(
            "/api/v1/activities/a1/climb-routes/0",
            json={"result": "completed"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["climb_result"] == "completed"

    def test_404_for_unknown_activity(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.patch(
            "/api/v1/activities/doesnotexist/climb-routes/0",
            json={"result": "completed"},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_404_for_a_rest_split(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")
        _add_split(
            engine,
            activity_id="a1",
            split_index=1,
            split_type="climb_rest",
            grade=None,
            result=None,
        )

        r = client.patch(
            "/api/v1/activities/a1/climb-routes/1",
            json={"result": "completed"},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_overrides_grade_and_returns_the_updated_split(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")
        _add_split(engine, activity_id="a1", split_index=0, grade=2)

        r = client.patch(
            "/api/v1/activities/a1/climb-routes/0",
            json={"grade": 5},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["climb_grade"] == 5

    def test_can_correct_grade_and_status_together(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")
        _add_split(engine, activity_id="a1", split_index=0, grade=2, result="attempt")

        r = client.patch(
            "/api/v1/activities/a1/climb-routes/0",
            json={"grade": 5, "result": "completed"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["climb_grade"] == 5
        assert r.json()["climb_result"] == "completed"

    def test_422_when_neither_grade_nor_result_given(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")
        _add_split(engine, activity_id="a1", split_index=0)

        r = client.patch(
            "/api/v1/activities/a1/climb-routes/0",
            json={},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_404_for_a_negative_grade(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")
        _add_split(engine, activity_id="a1", split_index=0)

        r = client.patch(
            "/api/v1/activities/a1/climb-routes/0",
            json={"grade": -1},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_grade_correction_updates_a_manually_added_route_too(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")

        add_r = client.post(
            "/api/v1/activities/a1/climb-routes",
            json={"grade": 2, "result": "attempt"},
            headers=auth_headers,
        )
        assert add_r.status_code == 200
        split_index = add_r.json()["split_index"]

        r = client.patch(
            f"/api/v1/activities/a1/climb-routes/{split_index}",
            json={"grade": 7},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["climb_grade"] == 7
        assert r.json()["climb_result"] == "attempt"


class TestPostClimbRoute:
    def test_creates_a_manual_route(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")

        r = client.post(
            "/api/v1/activities/a1/climb-routes",
            json={"grade": 3, "result": "attempt"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["climb_grade"] == 3
        assert body["climb_result"] == "attempt"
        assert body["is_manual"] is True
        assert body["duration_s"] is None

    def test_422_for_an_invalid_result(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")

        r = client.post(
            "/api/v1/activities/a1/climb-routes",
            json={"grade": 3, "result": "sent"},
            headers=auth_headers,
        )
        assert r.status_code == 422


class TestDeleteClimbRoute:
    def test_removes_a_manually_added_route(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")
        add = client.post(
            "/api/v1/activities/a1/climb-routes",
            json={"grade": 3, "result": "attempt"},
            headers=auth_headers,
        )
        split_index = add.json()["split_index"]

        r = client.delete(f"/api/v1/activities/a1/climb-routes/{split_index}", headers=auth_headers)
        assert r.status_code == 204

        detail = client.get("/api/v1/activities/a1", headers=auth_headers)
        assert detail.json()["splits"] == []

    def test_400_for_a_fit_derived_route(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(conn, activity_id="a1", sport="rock_climbing", sub_sport="bouldering")
        _add_split(engine, activity_id="a1", split_index=0)

        r = client.delete("/api/v1/activities/a1/climb-routes/0", headers=auth_headers)
        assert r.status_code == 400


class TestClimbComparisons:
    def test_404_for_unknown_activity(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.get("/api/v1/activities/doesnotexist/climb-comparisons", headers=auth_headers)
        assert r.status_code == 404

    def test_matches_sessions_within_the_duration_band(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(
                conn,
                activity_id="target",
                sport="rock_climbing",
                sub_sport="bouldering",
                duration_s=3600.0,
                distance_m=None,
            )
            # Within +/-15% of 3600s.
            seed_activity(
                conn,
                activity_id="in_band",
                local_date="2025-06-02",
                sport="rock_climbing",
                sub_sport="bouldering",
                duration_s=3900.0,
                distance_m=None,
            )
            # Outside the band.
            seed_activity(
                conn,
                activity_id="out_of_band",
                local_date="2025-06-03",
                sport="rock_climbing",
                sub_sport="bouldering",
                duration_s=7200.0,
                distance_m=None,
            )
        _add_split(engine, activity_id="in_band", split_index=0, result="completed")

        r = client.get("/api/v1/activities/target/climb-comparisons", headers=auth_headers)
        body = r.json()
        assert body["matched_count"] == 1
        assert body["rows"][0]["id"] == "in_band"
        assert body["rows"][0]["route_count"] == 1
        assert body["rows"][0]["max_completed_grade"] == 2

    def test_excludes_a_non_bouldering_activity_of_the_same_duration(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(
                conn,
                activity_id="target",
                sport="rock_climbing",
                sub_sport="bouldering",
                duration_s=3600.0,
                distance_m=None,
            )
            seed_activity(
                conn,
                activity_id="a_run",
                local_date="2025-06-02",
                sport="running",
                duration_s=3600.0,
            )

        r = client.get("/api/v1/activities/target/climb-comparisons", headers=auth_headers)
        assert r.json()["matched_count"] == 0


class TestClimbingSummary:
    def test_aggregates_sessions_in_range(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(
                conn,
                activity_id="a1",
                local_date="2025-06-01",
                sport="rock_climbing",
                sub_sport="bouldering",
                distance_m=None,
            )
            seed_activity(
                conn,
                activity_id="a2",
                local_date="2025-06-05",
                sport="rock_climbing",
                sub_sport="bouldering",
                distance_m=None,
            )
        _add_split(engine, activity_id="a1", split_index=0, grade=2, result="completed")
        _add_split(engine, activity_id="a1", split_index=2, grade=2, result="attempt")
        _add_split(engine, activity_id="a2", split_index=0, grade=4, result="completed")

        r = client.get(
            "/api/v1/activities/climbing-summary",
            params={"start_date": "2025-06-01", "end_date": "2025-06-30"},
            headers=auth_headers,
        )
        body = r.json()
        assert body["session_count"] == 2
        assert body["total_routes"] == 3
        assert body["max_completed_grade"] == 4
        assert body["total_climb_time_s"] == 180.0
        breakdown = {b["grade"]: b for b in body["grade_breakdown"]}
        assert breakdown[2] == {"grade": 2, "attempted": 1, "completed": 1}
        assert breakdown[4] == {"grade": 4, "attempted": 0, "completed": 1}

    def test_excludes_sessions_outside_the_date_range(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(
                conn,
                activity_id="a1",
                local_date="2025-05-01",
                sport="rock_climbing",
                sub_sport="bouldering",
                distance_m=None,
            )
        _add_split(engine, activity_id="a1", split_index=0)

        r = client.get(
            "/api/v1/activities/climbing-summary",
            params={"start_date": "2025-06-01", "end_date": "2025-06-30"},
            headers=auth_headers,
        )
        body = r.json()
        assert body["session_count"] == 0
        assert body["total_routes"] == 0
        assert body["grade_breakdown"] == []

    def test_excludes_a_non_bouldering_activity(
        self, client: TestClient, auth_headers: dict[str, str], engine: Engine
    ) -> None:
        with engine.connect() as conn:
            seed_activity(
                conn,
                activity_id="a1",
                local_date="2025-06-01",
                sport="rock_climbing",
                sub_sport="sport_climbing",
            )

        r = client.get(
            "/api/v1/activities/climbing-summary",
            params={"start_date": "2025-06-01", "end_date": "2025-06-30"},
            headers=auth_headers,
        )
        assert r.json()["session_count"] == 0
