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

from perseverer.adapters.fit_folder import IngestResult
from perseverer.adapters.garmin_connect import (
    GarminAuthRequired,
    GarminConnectAdapter,
    GarminRateLimitAborted,
    RateLimiter,
    RateLimitSettings,
    sync_garmin_connect,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity_source_link,
    athlete,
    day_rollup,
    health_observation,
    health_stream,
    metadata,
    raw_object,
    sleep_session,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID

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
        sleep_by_date: dict[str, dict[str, Any]] | None = None,
        raise_on_sleep_for: set[str] | None = None,
        hrv_by_date: dict[str, dict[str, Any] | None] | None = None,
        raise_on_hrv_for: set[str] | None = None,
        readiness_by_date: dict[str, list[dict[str, Any]]] | None = None,
        raise_on_readiness_for: set[str] | None = None,
        training_status_by_date: dict[str, dict[str, Any]] | None = None,
        raise_on_training_status_for: set[str] | None = None,
        hydration_by_date: dict[str, dict[str, Any]] | None = None,
        raise_on_hydration_for: set[str] | None = None,
        race_predictions: list[dict[str, Any]] | None = None,
        raise_on_race_predictions: bool = False,
        stress_by_date: dict[str, dict[str, Any]] | None = None,
        raise_on_stress_for: set[str] | None = None,
    ) -> None:
        self.activities = activities
        self.fit_bytes_by_id = fit_bytes_by_id
        self._raise_on_login = raise_on_login
        self._raise_on_download_for = raise_on_download_for or set()
        self.stats_by_date = stats_by_date or {}
        self._raise_on_stats_for = raise_on_stats_for or set()
        self.stats_calls: list[str] = []
        self.sleep_by_date = sleep_by_date or {}
        self._raise_on_sleep_for = raise_on_sleep_for or set()
        self.sleep_calls: list[str] = []
        self.hrv_by_date = hrv_by_date or {}
        self._raise_on_hrv_for = raise_on_hrv_for or set()
        self.hrv_calls: list[str] = []
        self.readiness_by_date = readiness_by_date or {}
        self._raise_on_readiness_for = raise_on_readiness_for or set()
        self.readiness_calls: list[str] = []
        self.training_status_by_date = training_status_by_date or {}
        self._raise_on_training_status_for = raise_on_training_status_for or set()
        self.training_status_calls: list[str] = []
        self.hydration_by_date = hydration_by_date or {}
        self._raise_on_hydration_for = raise_on_hydration_for or set()
        self.hydration_calls: list[str] = []
        self.race_predictions = race_predictions or []
        self._raise_on_race_predictions = raise_on_race_predictions
        self.race_predictions_calls: list[tuple[str, str]] = []
        self.stress_by_date = stress_by_date or {}
        self._raise_on_stress_for = raise_on_stress_for or set()
        self.stress_calls: list[str] = []

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

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        self.sleep_calls.append(cdate)
        if cdate in self._raise_on_sleep_for:
            raise GarminConnectTooManyRequestsError("429")
        # A dailySleepDTO with null start/end (nothing tracked that night) is a valid, real
        # response shape (see parse_daily_sleep_json) and a no-op ingest -- same reasoning as
        # get_stats()'s own default above.
        return self.sleep_by_date.get(
            cdate,
            {
                "dailySleepDTO": {
                    "calendarDate": cdate,
                    "sleepStartTimestampGMT": None,
                    "sleepEndTimestampGMT": None,
                }
            },
        )

    def get_hrv_data(self, cdate: str) -> dict[str, Any] | None:
        self.hrv_calls.append(cdate)
        if cdate in self._raise_on_hrv_for:
            raise GarminConnectTooManyRequestsError("429")
        # `None` (HRV not yet computed for this date) is a valid, real response shape
        # (see parse_daily_hrv_json) and a no-op ingest -- same reasoning as get_stats()'s and
        # get_sleep_data()'s own defaults above.
        return self.hrv_by_date.get(cdate, None)

    def get_training_readiness(self, cdate: str) -> list[dict[str, Any]]:
        self.readiness_calls.append(cdate)
        if cdate in self._raise_on_readiness_for:
            raise GarminConnectTooManyRequestsError("429")
        # A single minimal-but-valid reading (only calendarDate, matching get_stats()'s own
        # default convention above) is a no-op ingest -- deliberately *not* a bare `[]` like
        # get_race_predictions()'s own unconfigured default just below, since archive_raw_bytes
        # dedupes purely by content sha256 across *all* kinds (see archive.py) and two
        # identical `[]` payloads from different endpoints would silently collide onto one
        # raw_object row, hiding one of the two kinds entirely.
        return self.readiness_by_date.get(cdate, [{"calendarDate": cdate}])

    def get_training_status(self, cdate: str) -> dict[str, Any]:
        self.training_status_calls.append(cdate)
        if cdate in self._raise_on_training_status_for:
            raise GarminConnectTooManyRequestsError("429")
        # An empty dict (nothing computed yet for any of the three sections) is a valid, real
        # response shape (see parse_daily_training_status_json) and a no-op ingest.
        return self.training_status_by_date.get(cdate, {})

    def get_hydration_data(self, cdate: str) -> dict[str, Any]:
        self.hydration_calls.append(cdate)
        if cdate in self._raise_on_hydration_for:
            raise GarminConnectTooManyRequestsError("429")
        # {"calendarDate": ..., "valueInML": 0.0} is a minimal-but-valid hydration payload
        # (matches parse_hydration_json's own expectations) -- deliberately carries one more
        # field than get_stats()'s own bare `{"calendarDate": cdate}` default so the two never
        # produce byte-identical content (see the training-readiness default above for why that
        # matters: archive_raw_bytes dedupes purely by sha256 across all kinds).
        return self.hydration_by_date.get(cdate, {"calendarDate": cdate, "valueInML": 0.0})

    def get_race_predictions(
        self, *, startdate: str, enddate: str, _type: str = "daily"
    ) -> list[dict[str, Any]]:
        self.race_predictions_calls.append((startdate, enddate))
        if self._raise_on_race_predictions:
            raise GarminConnectTooManyRequestsError("429")
        return self.race_predictions

    def get_stress_data(self, cdate: str) -> dict[str, Any]:
        self.stress_calls.append(cdate)
        if cdate in self._raise_on_stress_for:
            raise GarminConnectTooManyRequestsError("429")
        # Deliberately not a bare `{"calendarDate": cdate}` -- that's byte-identical to
        # get_stats()'s own default for the same date, and archive_raw_bytes dedupes purely by
        # sha256 across *all* kinds (see get_training_readiness's own comment above), which
        # would silently collide the two onto one raw_object row.
        return self.stress_by_date.get(cdate, {"calendarDate": cdate, "maxStressLevel": -1})


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
    assert kinds == {
        "garmin_connect_json",
        "fit_activity",
        "garmin_connect_daily_summary_json",
        "garmin_connect_daily_sleep_json",
        "garmin_connect_daily_hrv_json",
        "garmin_connect_daily_training_readiness_json",
        "garmin_connect_daily_training_status_json",
        "garmin_connect_daily_hydration_json",
        "garmin_connect_race_predictions_json",
        "garmin_connect_daily_stress_json",
    }
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
    assert kinds == {
        "garmin_connect_daily_summary_json",
        "garmin_connect_daily_sleep_json",
        "garmin_connect_daily_hrv_json",
        "garmin_connect_daily_training_readiness_json",
        "garmin_connect_daily_training_status_json",
        "garmin_connect_daily_hydration_json",
        "garmin_connect_race_predictions_json",
        "garmin_connect_daily_stress_json",
    }
    assert resting_hr == 47


def test_wellness_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    """A 429 on the wellness loop breaks *that* loop only -- the independent sleep and HRV
    loops right after it still run, same precedent as the existing activities-loop-then-
    wellness-loop relationship (one loop's abort doesn't cascade into stopping a loop that
    hasn't started yet; only the next *scheduled run* recovers whatever this run's own aborted
    loop missed)."""
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
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )

    assert summary.errors  # the 429 is recorded, not silently swallowed
    # wellness aborted; every independent loop after it still ran
    assert kinds == {
        "garmin_connect_daily_sleep_json",
        "garmin_connect_daily_hrv_json",
        "garmin_connect_daily_training_readiness_json",
        "garmin_connect_daily_training_status_json",
        "garmin_connect_daily_hydration_json",
        "garmin_connect_race_predictions_json",
        "garmin_connect_daily_stress_json",
    }


def test_daily_sleep_is_fetched_archived_and_ingested(tmp_path: Path) -> None:
    """The full sync_garmin_connect() entrypoint, zero activities, one night's real sleep
    payload -- proves get_sleep_data() -> archive -> parse_daily_sleep_json ->
    ingest_health_batch end to end, landing a real row in sleep_session (previously this
    adapter never fetched sleep at all -- see adapters/garmin_connect.py's own docstring on
    fetch_and_ingest_daily_sleep)."""
    today = dt.datetime.now(dt.UTC).date().isoformat()
    start_ms = 1000
    end_ms = start_ms + 8 * 3600 * 1000  # 8 hours later

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            sleep_by_date={
                today: {
                    "dailySleepDTO": {
                        "calendarDate": today,
                        "sleepStartTimestampGMT": start_ms,
                        "sleepEndTimestampGMT": end_ms,
                        "sleepTimeSeconds": 8 * 3600,
                        "sleepScores": {"overall": {"value": 80}},
                    },
                    "sleepLevels": [],
                }
            },
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
            rolling_window_days=0,  # just today -- one get_sleep_data() call
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=factory,
        )
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        session_row = conn.execute(
            select(sleep_session).where(
                sleep_session.c.source == "garmin_connect",
                sleep_session.c.local_date == today,
            )
        ).fetchone()

    assert summary.errors == []
    assert "garmin_connect_daily_sleep_json" in kinds
    assert session_row is not None
    assert session_row.total_sleep_s == 8 * 3600
    assert session_row.sleep_score == 80.0


def test_sleep_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(activities=[], fit_bytes_by_id={}, raise_on_sleep_for={today})

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
        sleep_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_daily_sleep_json",
            )
        ).fetchall()

    assert summary.errors  # the 429 is recorded, not silently swallowed
    assert sleep_kinds == []  # nothing archived for the sleep fetch that 429'd


def test_daily_hrv_is_fetched_archived_and_ingested(tmp_path: Path) -> None:
    """The full sync_garmin_connect() entrypoint, zero activities, one day's real HRV payload
    -- proves get_hrv_data() -> archive -> parse_daily_hrv_json -> ingest_health_batch end to
    end, landing a real hrv_nightly_average-aliased observation (previously this adapter never
    fetched HRV at all -- see adapters/garmin_connect.py's own docstring on
    fetch_and_ingest_daily_hrv)."""
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            hrv_by_date={
                today: {
                    "hrvSummary": {
                        "calendarDate": today,
                        "weeklyAvg": 41,
                        "lastNightAvg": 37,
                        "status": "BALANCED",
                    }
                }
            },
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
            rolling_window_days=0,  # just today -- one get_hrv_data() call
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=factory,
        )
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        last_night_avg = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_hrv.lastNightAvg",
                health_observation.c.local_date == today,
            )
        ).scalar_one()

    assert summary.errors == []
    assert "garmin_connect_daily_hrv_json" in kinds
    assert last_night_avg == 37.0


def test_hrv_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(activities=[], fit_bytes_by_id={}, raise_on_hrv_for={today})

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
        hrv_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_daily_hrv_json",
            )
        ).fetchall()

    assert summary.errors  # the 429 is recorded, not silently swallowed
    assert hrv_kinds == []  # nothing archived for the HRV fetch that 429'd


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


def test_daily_training_readiness_is_fetched_archived_and_ingested(tmp_path: Path) -> None:
    """The full sync_garmin_connect() entrypoint, zero activities, one day's real training
    readiness payload -- proves get_training_readiness() -> archive ->
    parse_daily_training_readiness_json -> ingest_health_batch end to end (previously this
    adapter never fetched training readiness at all -- see adapters/garmin_connect.py's own
    docstring on fetch_and_ingest_daily_training_readiness)."""
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            readiness_by_date={
                today: [{"calendarDate": today, "timestamp": f"{today}T08:00:00.0", "score": 62}]
            },
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
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        score = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_training_readiness.score",
                health_observation.c.local_date == today,
            )
        ).scalar_one()

    assert summary.errors == []
    assert "garmin_connect_daily_training_readiness_json" in kinds
    assert score == 62.0


def test_training_readiness_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[], fit_bytes_by_id={}, raise_on_readiness_for={today}
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
        readiness_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_daily_training_readiness_json",
            )
        ).fetchall()
        # The loops after training readiness are independent -- a 429 there must not stop
        # training status from running.
        training_status_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_daily_training_status_json",
            )
        ).fetchall()

    assert summary.errors  # the 429 is recorded, not silently swallowed
    assert readiness_kinds == []  # nothing archived for the readiness fetch that 429'd
    assert training_status_kinds != []


def test_daily_training_status_is_fetched_archived_and_ingested(tmp_path: Path) -> None:
    """The full sync_garmin_connect() entrypoint, zero activities, one day's real training
    status payload -- proves get_training_status() -> archive ->
    parse_daily_training_status_json -> ingest_health_batch end to end, covering VO2max/heat-
    altitude/training-status in one fetch (previously never fetched live at all)."""
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            training_status_by_date={
                today: {
                    "mostRecentVO2Max": {
                        "generic": {"calendarDate": today, "vo2MaxValue": 48.0},
                    },
                    "mostRecentTrainingStatus": {
                        "latestTrainingStatusData": {
                            "3492916187": {
                                "calendarDate": today,
                                "weeklyTrainingLoad": 420,
                            }
                        }
                    },
                }
            },
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
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        vo2max = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_vo2max.vo2MaxValue",
                health_observation.c.local_date == today,
            )
        ).scalar_one()

    assert summary.errors == []
    assert "garmin_connect_daily_training_status_json" in kinds
    assert vo2max == 48.0


def test_training_status_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[], fit_bytes_by_id={}, raise_on_training_status_for={today}
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
        training_status_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_daily_training_status_json",
            )
        ).fetchall()

    assert summary.errors  # the 429 is recorded, not silently swallowed
    assert training_status_kinds == []  # nothing archived for the fetch that 429'd


def test_daily_hydration_is_fetched_archived_and_ingested(tmp_path: Path) -> None:
    """The full sync_garmin_connect() entrypoint, zero activities, one day's real hydration
    payload -- proves get_hydration_data() -> archive -> parse_hydration_json (reused, no new
    parser needed) -> ingest_health_batch end to end (previously this adapter only ever picked
    up hydration from a dropped fit_folder JSON file, never fetched it live)."""
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            hydration_by_date={today: {"calendarDate": today, "valueInML": 1500.0}},
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
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        value_in_ml = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.hydration.valueInML",
                health_observation.c.local_date == today,
            )
        ).scalar_one()

    assert summary.errors == []
    assert "garmin_connect_daily_hydration_json" in kinds
    assert value_in_ml == 1500.0


def test_hydration_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(
            activities=[], fit_bytes_by_id={}, raise_on_hydration_for={today}
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
        hydration_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_daily_hydration_json",
            )
        ).fetchall()
        # Race predictions is the loop after hydration -- a 429 in hydration must not stop it.
        race_prediction_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_race_predictions_json",
            )
        ).fetchall()

    assert summary.errors  # the 429 is recorded, not silently swallowed
    assert hydration_kinds == []  # nothing archived for the fetch that 429'd
    assert race_prediction_kinds != []


def test_race_predictions_is_fetched_archived_and_ingested_once_per_run(tmp_path: Path) -> None:
    """Unlike every other daily fetch, get_race_predictions() covers the whole rolling window
    in one call -- proves it's invoked exactly once per sync run, not once per day, and that
    parse_daily_race_predictions_json's per-record expansion lands real observations."""
    today = dt.datetime.now(dt.UTC).date().isoformat()

    client_holder: list[FakeGarminClient] = []

    def factory() -> FakeGarminClient:
        client = FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            race_predictions=[{"calendarDate": today, "time5K": 1320}],
        )
        client_holder.append(client)
        return client

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
            rolling_window_days=3,
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=factory,
        )
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        time_5k = conn.execute(
            select(health_observation.c.value_num).where(
                health_observation.c.metric_key == "garmin.daily_race_predictions.time5K",
                health_observation.c.local_date == today,
            )
        ).scalar_one()

    assert summary.errors == []
    assert "garmin_connect_race_predictions_json" in kinds
    assert time_5k == 1320.0
    assert len(client_holder[0].race_predictions_calls) == 1  # one range call, not one per day


def test_race_predictions_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    def factory() -> FakeGarminClient:
        return FakeGarminClient(activities=[], fit_bytes_by_id={}, raise_on_race_predictions=True)

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
        race_prediction_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_race_predictions_json",
            )
        ).fetchall()

    assert summary.errors  # the 429 is recorded, not silently swallowed
    assert race_prediction_kinds == []  # nothing archived for the fetch that 429'd


def test_body_battery_is_fetched_archived_and_ingested_per_date(tmp_path: Path) -> None:
    """Same per-date, whole-rolling-window shape as sleep/HRV above -- get_stress_data() (the
    dense body-battery source, see fetch_and_ingest_daily_body_battery's own docstring for why
    it replaced the sparse get_body_battery range endpoint) is invoked once per day, and
    parse_daily_stress_json's stream-point extraction lands real health_stream data. Descriptor
    list uses the real, plural `bodyBatteryValueDescriptorsDTOList` key confirmed live -- a
    different name than the old endpoint's singular one."""
    today = dt.datetime.now(dt.UTC).date().isoformat()

    client_holder: list[FakeGarminClient] = []

    def factory() -> FakeGarminClient:
        client = FakeGarminClient(
            activities=[],
            fit_bytes_by_id={},
            stress_by_date={
                today: {
                    "calendarDate": today,
                    "avgStressLevel": 23,
                    "bodyBatteryValuesArray": [[1748761200000, "MEASURED", 19, 3.0]],
                    "bodyBatteryValueDescriptorsDTOList": [
                        {
                            "bodyBatteryValueDescriptorIndex": 0,
                            "bodyBatteryValueDescriptorKey": "timestamp",
                        },
                        {
                            "bodyBatteryValueDescriptorIndex": 1,
                            "bodyBatteryValueDescriptorKey": "bodyBatteryStatus",
                        },
                        {
                            "bodyBatteryValueDescriptorIndex": 2,
                            "bodyBatteryValueDescriptorKey": "bodyBatteryLevel",
                        },
                    ],
                }
            },
        )
        client_holder.append(client)
        return client

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
            rolling_window_days=0,  # just today -- one get_stress_data() call
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=factory,
        )
        kinds = set(
            conn.execute(
                select(raw_object.c.kind).where(raw_object.c.source == "garmin_connect")
            ).scalars()
        )
        stream_row = conn.execute(
            select(health_stream.c.n_samples).where(
                health_stream.c.metric_key == "garmin.daily_body_battery.level"
            )
        ).fetchone()

    assert summary.errors == []
    assert "garmin_connect_daily_stress_json" in kinds
    assert stream_row is not None
    assert stream_row.n_samples == 1
    assert len(client_holder[0].stress_calls) == 1


def test_body_battery_429_aborts_the_run_without_retrying(tmp_path: Path) -> None:
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def factory() -> FakeGarminClient:
        return FakeGarminClient(activities=[], fit_bytes_by_id={}, raise_on_stress_for={today})

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
        body_battery_kinds = conn.execute(
            select(raw_object.c.id).where(
                raw_object.c.source == "garmin_connect",
                raw_object.c.kind == "garmin_connect_daily_stress_json",
            )
        ).fetchall()

    assert summary.errors  # the 429 is recorded, not silently swallowed
    assert body_battery_kinds == []  # nothing archived for the fetch that 429'd
