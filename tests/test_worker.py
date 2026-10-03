"""Tests for worker/main.py::run_daily_workout_push -- the date-window/push_status filtering and
rate-limit-abort behavior. run_daily_sync/run_daily_backup have no direct unit tests in this
codebase (their own building blocks -- sync_garmin_connect, create_backup -- are tested
directly); run_daily_workout_push gets one here because its window/filter logic is new and
worth guarding independent of push_planned_workout's own tests (tests/test_planned_workouts.py).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sqlalchemy import Engine, select

from perseverer.adapters.fit_folder import IngestRunSummary
from perseverer.config import Settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, athlete_email_report_config, metadata, planned_workout
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.worker.main import (
    run_daily_sync,
    run_daily_workout_push,
    run_monthly_email_report,
    run_weekly_email_report,
)

SECOND_ATHLETE_ID = "01SECONDATHLETE0000000000"


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
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
    return engine


def _insert(engine: Engine, *, local_date: str, push_status: str = "draft") -> int:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        result = conn.execute(
            planned_workout.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date=local_date,
                sport="running",
                name="Test",
                source_text="Warmup 10m",
                estimated_duration_s=600,
                push_status=push_status,
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        workout_id = result.inserted_primary_key[0]
        conn.commit()
    assert isinstance(workout_id, int)
    return workout_id


def test_only_pushes_workouts_due_within_the_window_and_not_already_pushed(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    in_window = _insert(engine, local_date=(today + dt.timedelta(days=3)).isoformat())
    already_pushed = _insert(
        engine, local_date=(today + dt.timedelta(days=1)).isoformat(), push_status="pushed"
    )
    too_far_out = _insert(engine, local_date=(today + dt.timedelta(days=30)).isoformat())
    in_the_past = _insert(engine, local_date=(today - dt.timedelta(days=1)).isoformat())

    calls: list[int] = []

    def fake_push(conn: Any, *, athlete_id: str, planned_workout_id: int, **kwargs: Any) -> Any:
        calls.append(planned_workout_id)
        from perseverer.planned_workouts import PushResult

        return PushResult(success=True, garmin_workout_id=1, error=None)

    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.push_planned_workout", side_effect=fake_push),
    ):
        run_daily_workout_push()

    assert calls == [in_window]
    assert already_pushed not in calls
    assert too_far_out not in calls
    assert in_the_past not in calls


# --- run_weekly_email_report -------------------------------------------------------------------


def _opt_in(engine: Engine, athlete_id: str, *, weekly: bool) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete_email_report_config.insert().values(
                athlete_id=athlete_id,
                weekly_enabled=weekly,
                monthly_enabled=False,
                updated_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
            )
        )
        conn.commit()


def test_weekly_email_report_only_sends_for_opted_in_athletes(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=SECOND_ATHLETE_ID,
                display_name="Two",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    _opt_in(engine, DEFAULT_ATHLETE_ID, weekly=True)  # second athlete does NOT opt in

    sent: list[str] = []
    settings = Settings(
        data_dir=tmp_path,
        smtp_host="ssl0.ovh.net",
        smtp_username="u",
        smtp_password="p",
        smtp_from="f@example.com",
    )
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch(
            "perseverer.worker.main.send_report_email",
            side_effect=lambda *a, **kw: sent.append(kw["athlete_id"]),
        ),
    ):
        run_weekly_email_report()

    assert sent == [DEFAULT_ATHLETE_ID]


def test_weekly_email_report_is_a_noop_when_smtp_unconfigured(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _opt_in(engine, DEFAULT_ATHLETE_ID, weekly=True)

    settings = Settings(data_dir=tmp_path)  # no PERSEVERER_SMTP_*
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.send_report_email") as mock_send,
    ):
        run_weekly_email_report()

    mock_send.assert_not_called()


def test_weekly_email_report_skips_an_athlete_with_no_email_without_crashing(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _opt_in(engine, DEFAULT_ATHLETE_ID, weekly=True)

    settings = Settings(
        data_dir=tmp_path,
        smtp_host="ssl0.ovh.net",
        smtp_username="u",
        smtp_password="p",
        smtp_from="f@example.com",
    )
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch(
            "perseverer.worker.main.send_report_email",
            side_effect=ValueError("athlete X has no email address set"),
        ),
    ):
        run_weekly_email_report()  # must not raise


def test_weekly_email_report_syncs_garmin_first_for_each_opted_in_athlete(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=SECOND_ATHLETE_ID,
                display_name="Two",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    _opt_in(engine, DEFAULT_ATHLETE_ID, weekly=True)
    _opt_in(engine, SECOND_ATHLETE_ID, weekly=True)

    synced: list[str] = []
    sent: list[str] = []

    def fake_sync_garmin_connect(
        conn: Any,
        raw_dir: Any,
        parquet_dir: Any,
        tokenstore_dir: Any,
        *,
        athlete_id: str,
        **kw: Any,
    ) -> IngestRunSummary:
        synced.append(athlete_id)
        return IngestRunSummary(run_id=0)

    settings = Settings(
        data_dir=tmp_path,
        smtp_host="ssl0.ovh.net",
        smtp_username="u",
        smtp_password="p",
        smtp_from="f@example.com",
    )
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.sync_garmin_connect", side_effect=fake_sync_garmin_connect),
        patch(
            "perseverer.worker.main.send_report_email",
            side_effect=lambda *a, **kw: sent.append(kw["athlete_id"]),
        ),
    ):
        run_weekly_email_report()

    assert set(synced) == {DEFAULT_ATHLETE_ID, SECOND_ATHLETE_ID}
    assert set(sent) == {DEFAULT_ATHLETE_ID, SECOND_ATHLETE_ID}


def test_weekly_email_still_sends_when_the_pre_send_garmin_sync_fails(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _opt_in(engine, DEFAULT_ATHLETE_ID, weekly=True)

    sent: list[str] = []
    settings = Settings(
        data_dir=tmp_path,
        smtp_host="ssl0.ovh.net",
        smtp_username="u",
        smtp_password="p",
        smtp_from="f@example.com",
    )
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch(
            "perseverer.worker.main.sync_garmin_connect",
            side_effect=RuntimeError("no token store yet"),
        ),
        patch(
            "perseverer.worker.main.send_report_email",
            side_effect=lambda *a, **kw: sent.append(kw["athlete_id"]),
        ),
    ):
        run_weekly_email_report()  # must not raise -- the send still goes out

    assert sent == [DEFAULT_ATHLETE_ID]


def test_weekly_email_sync_rate_limit_for_one_athlete_does_not_block_anothers_send(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=SECOND_ATHLETE_ID,
                display_name="Two",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    _opt_in(engine, DEFAULT_ATHLETE_ID, weekly=True)
    _opt_in(engine, SECOND_ATHLETE_ID, weekly=True)

    from perseverer.adapters.garmin_connect import GarminRateLimitAborted

    sent: list[str] = []

    def fake_sync_garmin_connect(
        conn: Any,
        raw_dir: Any,
        parquet_dir: Any,
        tokenstore_dir: Any,
        *,
        athlete_id: str,
        **kw: Any,
    ) -> IngestRunSummary:
        if athlete_id == DEFAULT_ATHLETE_ID:
            raise GarminRateLimitAborted("429")
        return IngestRunSummary(run_id=0)

    settings = Settings(
        data_dir=tmp_path,
        smtp_host="ssl0.ovh.net",
        smtp_username="u",
        smtp_password="p",
        smtp_from="f@example.com",
    )
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.sync_garmin_connect", side_effect=fake_sync_garmin_connect),
        patch(
            "perseverer.worker.main.send_report_email",
            side_effect=lambda *a, **kw: sent.append(kw["athlete_id"]),
        ),
    ):
        run_weekly_email_report()

    # Both athletes still get their email even though the first one's own pre-send sync hit
    # Garmin's rate limit.
    assert set(sent) == {DEFAULT_ATHLETE_ID, SECOND_ATHLETE_ID}


def test_monthly_email_report_does_not_sync_garmin_first(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete_email_report_config.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                weekly_enabled=False,
                monthly_enabled=True,
                updated_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
            )
        )
        conn.commit()

    settings = Settings(
        data_dir=tmp_path,
        smtp_host="ssl0.ovh.net",
        smtp_username="u",
        smtp_password="p",
        smtp_from="f@example.com",
    )
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.sync_garmin_connect") as mock_sync,
        patch("perseverer.worker.main.send_report_email"),
    ):
        run_monthly_email_report()

    mock_sync.assert_not_called()


def test_rate_limit_abort_stops_the_loop_without_marking_remaining_failed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    first = _insert(engine, local_date=today.isoformat())
    second = _insert(engine, local_date=(today + dt.timedelta(days=1)).isoformat())

    from perseverer.adapters.garmin_connect import GarminRateLimitAborted

    def fake_push(conn: Any, *, athlete_id: str, planned_workout_id: int, **kwargs: Any) -> Any:
        raise GarminRateLimitAborted("429")

    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.push_planned_workout", side_effect=fake_push),
    ):
        run_daily_workout_push()

    with engine.connect() as conn:
        rows = {
            r.id: r.push_status
            for r in conn.execute(select(planned_workout.c.id, planned_workout.c.push_status))
        }
    # Neither row was ever marked push_failed -- a rate-limit abort isn't this workout's fault
    # (push_planned_workout deliberately lets it propagate rather than writing push_status, see
    # its own docstring). Both belong to the same athlete, so once that athlete is rate-limited,
    # every further due workout of theirs this run is skipped too (see
    # test_rate_limit_for_one_athlete_does_not_block_another_athletes_push below for the
    # per-athlete isolation this is actually guarding).
    assert rows[first] == "draft"
    assert rows[second] == "draft"


def test_rate_limit_for_one_athlete_does_not_block_another_athletes_push(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=SECOND_ATHLETE_ID,
                display_name="Second",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    today = dt.datetime.now(dt.UTC).date()
    rate_limited_athletes_workout = _insert(engine, local_date=today.isoformat())
    with engine.connect() as conn:
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == rate_limited_athletes_workout)
            .values(athlete_id=DEFAULT_ATHLETE_ID)
        )
        conn.execute(
            planned_workout.insert().values(
                athlete_id=SECOND_ATHLETE_ID,
                local_date=today.isoformat(),
                sport="running",
                name="Test",
                source_text="Warmup 10m",
                estimated_duration_s=600,
                push_status="draft",
                created_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
                updated_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
            )
        )
        conn.commit()

    from perseverer.adapters.garmin_connect import GarminRateLimitAborted
    from perseverer.planned_workouts import PushResult

    calls: list[str] = []

    def fake_push(conn: Any, *, athlete_id: str, planned_workout_id: int, **kwargs: Any) -> Any:
        calls.append(athlete_id)
        if athlete_id == DEFAULT_ATHLETE_ID:
            raise GarminRateLimitAborted("429")
        return PushResult(success=True, garmin_workout_id=1, error=None)

    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.push_planned_workout", side_effect=fake_push),
    ):
        run_daily_workout_push()

    # Both athletes' due workouts were attempted -- the rate limit on DEFAULT_ATHLETE_ID didn't
    # stop SECOND_ATHLETE_ID's own push from being tried.
    assert set(calls) == {DEFAULT_ATHLETE_ID, SECOND_ATHLETE_ID}


def test_no_due_workouts_is_a_clean_noop(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.push_planned_workout") as mock_push,
    ):
        run_daily_workout_push()
    mock_push.assert_not_called()


# --- run_daily_sync ------------------------------------------------------------------------


def test_run_daily_sync_syncs_every_athlete_independently(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=SECOND_ATHLETE_ID,
                display_name="Second",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()

    synced_athletes: list[str] = []

    def fake_sync_garmin_connect(
        conn: Any,
        raw_dir: Any,
        parquet_dir: Any,
        tokenstore_dir: Any,
        *,
        athlete_id: str,
        **kw: Any,
    ) -> IngestRunSummary:
        synced_athletes.append(athlete_id)
        return IngestRunSummary(run_id=0)

    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.sync_garmin_connect", side_effect=fake_sync_garmin_connect),
    ):
        run_daily_sync()

    assert set(synced_athletes) == {DEFAULT_ATHLETE_ID, SECOND_ATHLETE_ID}


def test_run_daily_sync_one_athletes_failure_does_not_block_another(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=SECOND_ATHLETE_ID,
                display_name="Second",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()

    synced_athletes: list[str] = []

    def fake_sync_garmin_connect(
        conn: Any,
        raw_dir: Any,
        parquet_dir: Any,
        tokenstore_dir: Any,
        *,
        athlete_id: str,
        **kw: Any,
    ) -> IngestRunSummary:
        if athlete_id == DEFAULT_ATHLETE_ID:
            raise RuntimeError("unexpected failure for the first athlete")
        synced_athletes.append(athlete_id)
        return IngestRunSummary(run_id=0)

    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.sync_garmin_connect", side_effect=fake_sync_garmin_connect),
    ):
        run_daily_sync()  # must not raise -- a per-athlete failure is caught and logged

    assert synced_athletes == [SECOND_ATHLETE_ID]


# --- scheduled Kaya import -----------------------------------------------------------------


def _run_sync_with_kaya(tmp_path: Path, *, with_tokens: bool, kaya_effect: Any = None) -> list[str]:
    from perseverer.adapters.kaya import KayaTokens, save_tokens
    from perseverer.adapters.kaya_ingest import KayaImportSummary

    engine = _engine(tmp_path)
    settings = Settings(data_dir=tmp_path)
    if with_tokens:
        save_tokens(
            settings.kaya_tokenstore_dir_for(DEFAULT_ATHLETE_ID),
            KayaTokens("t", "r", "1", dt.datetime.now(dt.UTC).isoformat()),
        )
    called: list[str] = []

    def fake_import(conn: Any, raw_dir: Any, *, athlete_id: str, tokenstore_dir: Any) -> Any:
        called.append(athlete_id)
        if kaya_effect is not None:
            raise kaya_effect
        return KayaImportSummary(pages=1, sessions=2, ascents=3, touched_dates=1)

    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch(
            "perseverer.worker.main.sync_garmin_connect",
            side_effect=lambda *a, **k: IngestRunSummary(run_id=0),
        ),
        patch("perseverer.worker.main.import_kaya", side_effect=fake_import),
    ):
        run_daily_sync()
    return called


def test_daily_sync_imports_kaya_after_garmin_for_an_athlete_who_logged_in(tmp_path: Path) -> None:
    assert _run_sync_with_kaya(tmp_path, with_tokens=True) == [DEFAULT_ATHLETE_ID]


def test_daily_sync_skips_kaya_for_an_athlete_who_never_logged_in(tmp_path: Path) -> None:
    assert _run_sync_with_kaya(tmp_path, with_tokens=False) == []


def test_a_kaya_failure_never_breaks_the_daily_sync(tmp_path: Path) -> None:
    from perseverer.adapters.kaya import KayaAuthRequired
    from perseverer.adapters.kaya_ingest import KayaRateLimited

    for i, effect in enumerate(
        (KayaAuthRequired("expired"), KayaRateLimited("429"), RuntimeError("boom"))
    ):
        run_dir = tmp_path / f"run{i}"
        run_dir.mkdir()
        # must not raise: logged, and the staleness check that follows still runs
        assert _run_sync_with_kaya(run_dir, with_tokens=True, kaya_effect=effect) == [
            DEFAULT_ATHLETE_ID
        ]
