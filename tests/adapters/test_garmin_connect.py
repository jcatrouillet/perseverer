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
from sporthealth.db.schema import (
    activity_source_link,
    athlete,
    day_rollup,
    health_observation,
    metadata,
    raw_object,
)
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
        stats_by_date: dict[str, dict[str, Any]] | None = None,
        raise_on_stats_for: set[str] | None = None,
    ) -> None:
        self.activities = activities
        self.fit_bytes_by_id = fit_bytes_by_id
        self._raise_on_login = raise_on_login
        self._raise_on_download_for = raise_on_download_for or set()
        self.stats_by_date = stats_by_date or {}
        self._raise_on_stats_for = raise_on_stats_for or set()
        self.stats_calls: list[str] = []

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

    def get_stats(self, cdate: str) -> dict[str, Any]:
        self.stats_calls.append(cdate)
        if cdate in self._raise_on_stats_for:
            raise GarminConnectTooManyRequestsError("429")
        # A bare {"calendarDate": ...} is a minimal-but-valid daily-summary payload (matches
        # real get_stats()/parse_daily_summary_json shape) -- an empty/no-op ingest for any date
        # a test doesn't explicitly configure, so existing activity-focused tests that exercise
        # the full sync_garmin_connect() entrypoint don't need to know about wellness data at all.
        return self.stats_by_date.get(cdate, {"calendarDate": cdate})


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
    assert kinds == {"garmin_connect_json", "fit_activity", "garmin_connect_daily_summary_json"}
    # Proves the rollup-refresh wiring end to end (ADR 0006 decision 3), not just in isolation --
    # the daily-wellness loop now touches every date in the rolling window too (11 dates at
    # rolling_window_days=10), so more than one day_rollup row exists; the one for the activity's
    # own date (wherever the fixture's embedded date lands) is what actually matters here.
    activity_rollup_rows = [r for r in rollup_rows if r.activity_count == 1]
    assert len(activity_rollup_rows) == 1


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


def test_daily_wellness_is_fetched_archived_and_ingested(tmp_path: Path) -> None:
    """The full sync_garmin_connect() entrypoint, zero activities, one day's real wellness
    payload -- proves get_stats() -> archive -> parse_daily_summary_json -> ingest_health_batch
    end to end, reusing the exact parser fit_folder.py already uses for this JSON shape."""
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            stats_by_date={today: {"calendarDate": today, "restingHeartRate": 47}},
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
            rolling_window_days=0,  # just today -- one get_stats() call
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=factory,
        )
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        resting_hr = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_summary.restingHeartRate",
                health_observation.c.local_date == today,
            )
        ).scalar_one()

    assert summary.errors == []
    assert kinds == {"garmin_connect_daily_summary_json"}
    assert resting_hr == 47


def test_wellness_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[], fit_bytes_by_id={}, raise_on_stats_for={today}
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
            rolling_window_days=0,
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=factory,
        )
        archived = conn.execute(
            select(raw_object.c.id).where(raw_object.c.source == "garmin_connect")
        ).fetchall()

    assert summary.errors  # the 429 is recorded, not silently swallowed
    assert archived == []  # nothing archived -- the run stopped before any wellness fetch landed


def test_wellness_alone_triggers_the_rollup_refresh_chain(tmp_path: Path) -> None:
    """Zero new activities, one day of wellness data -- touched_dates from wellness alone must
    still drive refresh_daily_and_period_rollups (ADR 0006 decision 3), the same as an
    activity-touched date would."""
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            stats_by_date={today: {"calendarDate": today, "totalSteps": 5000}},
        )

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        sync_garmin_connect(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "tokens",
            athlete_id=DEFAULT_ATHLETE_ID,
            rolling_window_days=0,
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=factory,
        )
        rollup_row = conn.execute(
            select(day_rollup).where(day_rollup.c.local_date == today)
        ).fetchone()

    assert rollup_row is not None
