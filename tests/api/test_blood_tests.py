"""Tests for GET/POST/PUT/DELETE /blood-tests -- athlete-entered blood test results. Covers the
router's own responsibilities: id-keyed CRUD, the date-range list route, the batch-create route
for a whole panel, the by-date panel-delete route, and request validation (reference range
ordering, blank marker names). Athlete-scoping (a mismatched athlete_id 404s rather than touching
the row) follows the same WHERE-clause pattern every by-id route in this codebase uses, verified
generically in tests/api/test_auth.py rather than duplicated per router.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def _create(client: TestClient, auth_headers: dict[str, str], **fields: Any) -> dict[str, Any]:
    body = {
        "local_date": "2026-01-15",
        "marker": "LDL Cholesterol",
        "value_num": 110.0,
        **fields,
    }
    r = client.post("/api/v1/blood-tests", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_get_by_id_404s_for_nonexistent_result(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/blood-tests/999999", headers=auth_headers)
    assert r.status_code == 404


def test_list_is_empty_when_no_results_in_range(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/api/v1/blood-tests?start_date=2020-01-01&end_date=2020-12-31", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() == []


def test_post_creates_with_optional_fields_defaulting_to_null(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _create(
        client,
        auth_headers,
        unit="mg/dL",
        reference_low=0.0,
        reference_high=130.0,
        lab_name="Quest Diagnostics",
        notes="Fasting draw",
    )
    assert body["local_date"] == "2026-01-15"
    assert body["marker"] == "LDL Cholesterol"
    assert body["value_num"] == 110.0
    assert body["unit"] == "mg/dL"
    assert body["reference_low"] == 0.0
    assert body["reference_high"] == 130.0
    assert body["lab_name"] == "Quest Diagnostics"
    assert body["notes"] == "Fasting draw"
    assert body["id"] > 0

    body_minimal = _create(client, auth_headers, marker="HDL Cholesterol", value_num=55.0)
    assert body_minimal["unit"] is None
    assert body_minimal["reference_low"] is None
    assert body_minimal["reference_high"] is None
    assert body_minimal["lab_name"] is None
    assert body_minimal["notes"] is None


def test_post_rejects_a_blank_marker(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post(
        "/api/v1/blood-tests",
        json={"local_date": "2026-01-15", "marker": "   ", "value_num": 100.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_post_rejects_a_reference_range_with_low_above_high(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/blood-tests",
        json={
            "local_date": "2026-01-15",
            "marker": "Glucose",
            "value_num": 90.0,
            "reference_low": 100.0,
            "reference_high": 70.0,
        },
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_post_rejects_an_invalid_local_date(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/blood-tests",
        json={"local_date": "not-a-date", "marker": "Glucose", "value_num": 90.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_list_orders_by_date_descending_then_marker(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _create(client, auth_headers, local_date="2026-01-15", marker="Zinc", value_num=1.0)
    _create(client, auth_headers, local_date="2026-01-15", marker="Ferritin", value_num=2.0)
    _create(client, auth_headers, local_date="2026-06-01", marker="Ferritin", value_num=3.0)

    r = client.get(
        "/api/v1/blood-tests?start_date=2026-01-01&end_date=2026-12-31", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert [(row["local_date"], row["marker"]) for row in body] == [
        ("2026-06-01", "Ferritin"),
        ("2026-01-15", "Ferritin"),
        ("2026-01-15", "Zinc"),
    ]


def test_batch_creates_every_marker_sharing_the_panels_own_lab_and_notes(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/blood-tests/batch",
        json={
            "local_date": "2026-03-01",
            "lab_name": "LabCorp",
            "notes": "Annual physical",
            "results": [
                {"marker": "Total Cholesterol", "value_num": 180.0, "unit": "mg/dL"},
                {
                    "marker": "HbA1c",
                    "value_num": 5.4,
                    "unit": "%",
                    "reference_low": 4.0,
                    "reference_high": 5.6,
                },
            ],
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 2
    assert {row["marker"] for row in body} == {"Total Cholesterol", "HbA1c"}
    for row in body:
        assert row["local_date"] == "2026-03-01"
        assert row["lab_name"] == "LabCorp"
        assert row["notes"] == "Annual physical"
    hba1c = next(row for row in body if row["marker"] == "HbA1c")
    assert hba1c["reference_low"] == 4.0
    assert hba1c["reference_high"] == 5.6


def test_batch_rejects_an_empty_results_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/blood-tests/batch",
        json={"local_date": "2026-03-01", "results": []},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_put_updates_all_fields(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _create(client, auth_headers)
    r = client.put(
        f"/api/v1/blood-tests/{created['id']}",
        json={
            "local_date": "2026-02-01",
            "marker": "LDL Cholesterol",
            "value_num": 95.0,
            "unit": "mg/dL",
            "reference_high": 100.0,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["local_date"] == "2026-02-01"
    assert body["value_num"] == 95.0
    assert body["reference_high"] == 100.0


def test_put_404s_for_nonexistent_result(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.put(
        "/api/v1/blood-tests/999999",
        json={"local_date": "2026-01-15", "marker": "LDL Cholesterol", "value_num": 100.0},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_delete_removes_one_result(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _create(client, auth_headers)
    r = client.delete(f"/api/v1/blood-tests/{created['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert (
        client.get(f"/api/v1/blood-tests/{created['id']}", headers=auth_headers).status_code == 404
    )


def test_delete_404s_for_nonexistent_result(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.delete("/api/v1/blood-tests/999999", headers=auth_headers)
    assert r.status_code == 404


def test_delete_by_date_removes_every_marker_on_that_date_only(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _create(client, auth_headers, local_date="2026-04-01", marker="LDL")
    _create(client, auth_headers, local_date="2026-04-01", marker="HDL")
    _create(client, auth_headers, local_date="2026-05-01", marker="LDL")

    r = client.delete("/api/v1/blood-tests/by-date/2026-04-01", headers=auth_headers)
    assert r.status_code == 200

    remaining = client.get(
        "/api/v1/blood-tests?start_date=2026-01-01&end_date=2026-12-31", headers=auth_headers
    ).json()
    assert len(remaining) == 1
    assert remaining[0]["local_date"] == "2026-05-01"


def test_delete_by_date_404s_when_nothing_recorded_that_date(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.delete("/api/v1/blood-tests/by-date/2020-01-01", headers=auth_headers)
    assert r.status_code == 404
