"""Tests for GET /fitness -- reads fitness_daily_rollup directly (seeded here, isolating the
API-layer test from the EWMA computation itself, which tests/test_fitness.py already covers).
"""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import fitness_daily_rollup
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def test_fitness_returns_rows_in_range_ordered_by_date(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        for local_date, load, ctl, atl, tsb in [
            ("2025-06-02", 100.0, 2.35, 13.31, 0.0),
            ("2025-06-01", 0.0, 0.0, 0.0, 0.0),
            ("2025-07-01", 50.0, 5.0, 8.0, -3.0),
        ]:
            conn.execute(
                fitness_daily_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date=local_date,
                    training_load=load,
                    ctl=ctl,
                    atl=atl,
                    tsb=tsb,
                    refreshed_at=now,
                )
            )
        conn.commit()

    r = client.get(
        "/api/v1/fitness?start_date=2025-06-01&end_date=2025-06-30", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert [row["local_date"] for row in body] == ["2025-06-01", "2025-06-02"]
    assert body[1]["ctl"] == 2.35
    assert body[1]["tsb"] == 0.0
