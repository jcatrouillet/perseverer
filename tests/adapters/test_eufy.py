"""eufy adapter tests against a fake client (no real network calls, ever) -- covering the
credential-skip contract, per-reading resilience (one bad record must not abort the run), and
idempotent re-runs (content-addressed archiving + upsert dedupe the same 539-record history a
real daily run would refetch every time).
"""

import datetime as dt
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select

from sporthealth.adapters.eufy import EufyAuthError, EufyClient, sync_eufy
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import athlete, health_observation, raw_object
from sporthealth.db.seed import DEFAULT_ATHLETE_ID

RECORD_1 = {
    "id": "record-1",
    "device_id": "dev",
    "user_id": "user",
    "customer_id": "cust",
    "group_id": "",
    "create_time": 1717200000,  # 2024-06-01T00:00:00Z
    "update_time": 1717200010,
    "scale_data": {"weight": 772, "bmi": 23.1, "body_fat": 19.4},
    "status": 0,
    "product_code": "eufy T9147",
}

RECORD_2 = {
    "id": "record-2",
    "device_id": "dev",
    "user_id": "user",
    "customer_id": "cust",
    "group_id": "",
    "create_time": 1717286400,  # 2024-06-02T00:00:00Z
    "update_time": 1717286410,
    "scale_data": {"weight": 770, "bmi": 23.0, "body_fat": 19.2},
    "status": 0,
    "product_code": "eufy T9147",
}


class FakeEufyClient(EufyClient):
    """Stands in for `EufyClient` -- subclasses it (rather than duck-typing) purely so it
    satisfies `sync_eufy()`'s `client_factory: Callable[..., EufyClient]` typing; none of the
    real HTTP-calling methods are ever invoked."""

    def __init__(self, email: str, password: str, device_id: str, customer_id: str) -> None:
        super().__init__(email, password, device_id, customer_id)
        self.readings: list[dict[str, Any]] = [RECORD_1, RECORD_2]
        self.raise_on_login: Exception | None = None

    def login(self) -> None:
        if self.raise_on_login:
            raise self.raise_on_login

    def fetch_all_readings(self) -> list[dict[str, Any]]:
        return self.readings


def _seed_athlete(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()


def _run(tmp_path: Path, client: FakeEufyClient) -> Any:
    engine = make_engine(tmp_path / "db.sqlite")
    from sporthealth.db.schema import metadata

    metadata.create_all(engine)
    _seed_athlete(engine)

    def factory(email: str, password: str, device_id: str, customer_id: str) -> FakeEufyClient:
        return client

    with engine.connect() as conn:
        summary = sync_eufy(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            email="e@example.com",
            password="pw",
            device_id="dev",
            customer_id="cust",
            client_factory=factory,
        )
    return engine, summary


def test_skips_without_error_when_credentials_missing(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "db.sqlite")
    from sporthealth.db.schema import metadata

    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = sync_eufy(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            email=None,
            password="pw",
            device_id="dev",
            customer_id="cust",
        )
    assert summary.items_seen == 0
    assert summary.errors == []


def test_login_failure_is_captured_not_raised(tmp_path: Path) -> None:
    client = FakeEufyClient("e", "p", "d", "c")
    client.raise_on_login = EufyAuthError("bad password")
    _, summary = _run(tmp_path, client)
    assert summary.items_seen == 0
    assert len(summary.errors) == 1
    assert "bad password" in summary.errors[0]["error"]


def test_full_sync_archives_and_ingests_every_reading(tmp_path: Path) -> None:
    client = FakeEufyClient("e", "p", "d", "c")
    engine, summary = _run(tmp_path, client)

    assert summary.items_seen == 2
    assert summary.items_new == 2
    assert summary.errors == []

    with engine.connect() as conn:
        raw_rows = conn.execute(select(raw_object)).fetchall()
        assert len(raw_rows) == 2

        obs_rows = conn.execute(
            select(health_observation).where(health_observation.c.metric_key == "eufy.scale.weight")
        ).fetchall()
        assert len(obs_rows) == 2
        weights = {round(r.value_num, 1) for r in obs_rows}
        assert weights == {77.2, 77.0}


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    client = FakeEufyClient("e", "p", "d", "c")
    engine, _ = _run(tmp_path, client)

    def factory(email: str, password: str, device_id: str, customer_id: str) -> FakeEufyClient:
        return client

    with engine.connect() as conn:
        summary2 = sync_eufy(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            email="e@example.com",
            password="pw",
            device_id="dev",
            customer_id="cust",
            client_factory=factory,
        )

    assert summary2.items_seen == 2
    assert summary2.items_new == 0  # nothing new second time around

    with engine.connect() as conn:
        raw_rows = conn.execute(select(raw_object)).fetchall()
        assert len(raw_rows) == 2  # content-addressed, not duplicated

        obs_rows = conn.execute(
            select(health_observation).where(health_observation.c.metric_key == "eufy.scale.weight")
        ).fetchall()
        assert len(obs_rows) == 2  # upserted, not duplicated


def test_one_bad_reading_does_not_abort_the_rest(tmp_path: Path) -> None:
    client = FakeEufyClient("e", "p", "d", "c")
    bad_record = {k: v for k, v in RECORD_1.items() if k != "create_time"}
    bad_record["id"] = "record-bad"
    client.readings = [bad_record, RECORD_2]

    engine, summary = _run(tmp_path, client)

    assert summary.items_seen == 2
    assert summary.items_new == 1
    assert len(summary.errors) == 1
    assert summary.errors[0]["reading"] == "record-bad"

    with engine.connect() as conn:
        obs_rows = conn.execute(
            select(health_observation).where(health_observation.c.metric_key == "eufy.scale.weight")
        ).fetchall()
        assert len(obs_rows) == 1
