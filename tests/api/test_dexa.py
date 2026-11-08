"""Tests for /dexa-scans: record a scan, replace on re-post, list newest first, delete."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _post(client: TestClient, headers: dict[str, str], date: str, fat: float) -> dict[str, object]:
    r = client.post(
        "/api/v1/dexa-scans",
        json={
            "local_date": date,
            "provider": "BodySpec",
            "values": [
                {"region": "total", "metric": "fat_pct", "value_num": fat},
                {"region": "arms", "metric": "lean_mass_kg", "value_num": 7.5},
            ],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_post_then_list_groups_values_by_scan_newest_first(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _post(client, auth_headers, "2025-11-23", 23.8)
    _post(client, auth_headers, "2026-05-02", 21.0)
    scans = client.get("/api/v1/dexa-scans", headers=auth_headers).json()
    assert [s["local_date"] for s in scans] == ["2026-05-02", "2025-11-23"]
    assert scans[1]["provider"] == "BodySpec"
    assert {(v["region"], v["metric"]): v["value_num"] for v in scans[1]["values"]} == {
        ("arms", "lean_mass_kg"): 7.5,
        ("total", "fat_pct"): 23.8,
    }


def test_posting_the_same_scan_twice_is_idempotent_and_corrects_values(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _post(client, auth_headers, "2025-11-23", 23.8)
    scan = _post(client, auth_headers, "2025-11-23", 24.1)
    assert len(scan["values"]) == 2  # type: ignore[arg-type]
    assert len(client.get("/api/v1/dexa-scans", headers=auth_headers).json()) == 1
    values = {(v["region"], v["metric"]): v["value_num"] for v in scan["values"]}  # type: ignore[attr-defined]
    assert values[("total", "fat_pct")] == 24.1


def test_delete_removes_the_scan_and_404s_when_absent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _post(client, auth_headers, "2025-11-23", 23.8)
    assert client.delete("/api/v1/dexa-scans/2025-11-23", headers=auth_headers).status_code == 200
    assert client.get("/api/v1/dexa-scans", headers=auth_headers).json() == []
    assert client.delete("/api/v1/dexa-scans/2025-11-23", headers=auth_headers).status_code == 404


def test_rejects_bad_date_and_empty_values(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    bad = client.post(
        "/api/v1/dexa-scans",
        json={
            "local_date": "11/23/2025",
            "values": [{"region": "total", "metric": "x", "value_num": 1}],
        },
        headers=auth_headers,
    )
    assert bad.status_code == 422
    empty = client.post(
        "/api/v1/dexa-scans", json={"local_date": "2025-11-23", "values": []}, headers=auth_headers
    )
    assert empty.status_code == 422
