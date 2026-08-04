"""garmin_connect tests against a fake client (no real network calls, ever) — covering the
three properties that matter most for an adapter this failure-prone: it never falls back to a
credentialed login, it aborts immediately on a 429 rather than retrying, and every synced
activity's original FIT gets archived alongside its JSON summary.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
from sqlalchemy import Connection, Engine, select

from sporthealth.adapters.fit_folder import IngestResult
from sporthealth.adapters.garmin_connect import (
    GarminAuthRequired,
    GarminConnectAdapter,
    GarminRateLimitAborted,
    RateLimiter,
    RateLimitSettings,
    sync_garmin_connect,
)
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity_source_link, athlete, day_rollup, metadata, raw_object
from sporthealth.db.seed import DEFAULT_ATHLETE_ID

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_run.fit"


class FakeGarminClient:
    """Stands in for `garminconnect.Garmin`. Deliberately has no `__init__` parameters that
    could carry credentials — `GarminConnectAdapter` only ever constructs its client via a
    zero-argument factory (`Callable[[], Garmin]`), so there is no code path through which
    real credentials could reach an automatic/scheduled run even in principle.
    """

    def __init__(
        self,
        activities: list[dict[str, Any]],
        fit_bytes_by_id: dict[str, bytes],
        raise_on_login: Exception | None = None,
        raise_on_download_for: set[str] | None = None,
    ) -> None:
        self.activities = activities
        self.fit_bytes_by_id = fit_bytes_by_id
        self._raise_on_login = raise_on_login
        self._raise_on_download_for = raise_on_download_for or set()

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        if self._raise_on_login:
            raise self._raise_on_login
        return None, None

    def get_activities_by_date(self, start: str, end: str) -> list[dict[str, Any]]:
        return self.activities

    def download_activity(self, activity_id: str, dl_fmt: object = None) -> bytes:
        if activity_id in self._raise_on_download_for:
            raise GarminConnectTooManyRequestsError("429")
        return self.fit_bytes_by_id[activity_id]


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


def _adapter(tmp_path: Path, **client_kwargs: Any) -> GarminConnectAdapter:
    def factory() -> FakeGarminClient:
        return FakeGarminClient(**client_kwargs)

    return GarminConnectAdapter(tmp_path / "tokens", RateLimiter(0, 999), client_factory=factory)


def _fetch(
    adapter: GarminConnectAdapter,
    conn: Connection,
    tmp_path: Path,
    activity_summary: dict[str, Any],
) -> IngestResult:
    return adapter.fetch_and_ingest_activity(
        conn,
        tmp_path / "archive",
        tmp_path / "parquet",
        athlete_id=DEFAULT_ATHLETE_ID,
        activity_summary=activity_summary,
    )


def test_authenticate_never_falls_back_to_credentials(tmp_path: Path) -> None:
    """If the token store fails to load, the adapter must fail loudly, not attempt a
    credentialed login — proven here without any real Garmin token store or network access.
    """
    adapter = _adapter(
        tmp_path,
        activities=[],
        fit_bytes_by_id={},
        raise_on_login=GarminConnectAuthenticationError("no token"),
    )
    with pytest.raises(GarminAuthRequired):
        adapter.authenticate()


def test_rate_limit_abort_stops_the_run_without_retrying(tmp_path: Path) -> None:
    activities = [
        {"activityId": "1"},
        {"activityId": "2"},  # this one 429s
        {"activityId": "3"},  # must never be reached
    ]
    fit_bytes = FIXTURE.read_bytes()
    adapter = _adapter(
        tmp_path,
        activities=activities,
        fit_bytes_by_id={"1": fit_bytes, "3": fit_bytes},
        raise_on_download_for={"2"},
    )

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        adapter.authenticate()
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=10)
        summary_activities = adapter.list_activity_summaries(since)

        seen = 0
        aborted = False
        for a in summary_activities:
            seen += 1
            try:
                _fetch(adapter, conn, tmp_path, a)
                conn.commit()
            except GarminRateLimitAborted:
                conn.rollback()
                aborted = True
                break

        assert aborted
        assert seen == 2  # activity "3" was never attempted

        linked_ids = set(
            conn.execute(
                select(activity_source_link.c.external_id).where(
                    activity_source_link.c.source == "garmin_connect"
                )
            ).scalars()
        )
    assert linked_ids == {"1"}  # only the activity before the 429 got ingested


def test_sync_garmin_connect_without_a_token_store_fails_cleanly(tmp_path: Path) -> None:
    """The full orchestration, with the *real* Garmin() factory and no token store: must fail
    the ingest_run (for the staleness check to pick up) rather than raise uncaught or attempt
    a credentialed login."""
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = sync_garmin_connect(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "tokens",
            athlete_id=DEFAULT_ATHLETE_ID,
            rolling_window_days=10,
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
        )
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )

    assert summary.errors
    assert kinds == set()  # nothing archived — auth failed before any fetch was attempted


def test_sync_garmin_connect_full_orchestration_with_fake_client(tmp_path: Path) -> None:
    """The same entrypoint the scheduler and `sync import garmin-connect` use, end to end,
    with a fake client injected — proves ingest_run bookkeeping and archiving together."""
    fit_bytes = FIXTURE.read_bytes()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[{"activityId": "42"}], fit_bytes_by_id={"42": fit_bytes}
        )

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = sync_garmin_connect(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "tokens",
            athlete_id=DEFAULT_ATHLETE_ID,
            rolling_window_days=10,
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=factory,
        )
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        rollup_rows = conn.execute(select(day_rollup)).fetchall()

    assert summary.errors == []
    assert summary.items_seen == 1
    assert summary.items_new == 1
    assert kinds == {"garmin_connect_json", "fit_activity"}
    # Proves the rollup-refresh wiring end to end (ADR 0006 decision 3), not just in isolation.
    assert len(rollup_rows) == 1
    assert rollup_rows[0].activity_count == 1


def test_full_sync_with_injected_fake_client_archives_and_ingests(tmp_path: Path) -> None:
    fit_bytes = FIXTURE.read_bytes()
    adapter = _adapter(
        tmp_path, activities=[{"activityId": "42"}], fit_bytes_by_id={"42": fit_bytes}
    )

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        adapter.authenticate()
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=10)
        for a in adapter.list_activity_summaries(since):
            _fetch(adapter, conn, tmp_path, a)
        conn.commit()

        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        external_id = conn.execute(
            select(activity_source_link.c.external_id).where(
                activity_source_link.c.source == "garmin_connect"
            )
        ).scalar_one()

    assert kinds == {"garmin_connect_json", "fit_activity"}
    assert external_id == "42"  # Garmin's own activityId, not a derived heuristic


def test_idempotent_resync_is_a_noop(tmp_path: Path) -> None:
    fit_bytes = FIXTURE.read_bytes()
    adapter = _adapter(
        tmp_path, activities=[{"activityId": "42"}], fit_bytes_by_id={"42": fit_bytes}
    )

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        adapter.authenticate()
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=10)
        activities = adapter.list_activity_summaries(since)

        first = _fetch(adapter, conn, tmp_path, activities[0])
        conn.commit()
        second = _fetch(adapter, conn, tmp_path, activities[0])
        conn.commit()

    assert first.created
    assert not second.created
    assert first.activity_id == second.activity_id
