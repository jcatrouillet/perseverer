"""Worker container entrypoint: runs the daily garmin_connect sync + staleness check on a
cron schedule (default 04:15 UTC, jittered — see §6 of the project spec), a separate daily
backup job (Phase 9, ADR 0014, default 03:30 UTC — see backup.py), and a separate daily
scheduled-workout push job (default 04:45 UTC, right after the sync — see planned_workouts.py).
All three schedules resolve against `schedule_timezone` (an IANA name, default UTC), not the
container's own system clock -- see that setting's own docstring in config.py for why a real
timezone rather than a fixed hour offset matters here (DST).

`garmin_export` is deliberately never scheduled here — it's a one-off/occasional CLI action
(`sync import garmin-export <path>`), not a recurring job. Same for `sync backup restore` --
CLI-only, human-initiated, never automated (see backup.py's own docstring for why).
"""

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from perseverer.adapters.eufy import sync_eufy
from perseverer.adapters.garmin_connect import (
    GarminRateLimitAborted,
    RateLimitSettings,
    sync_garmin_connect,
)
from perseverer.backup import create_backup
from perseverer.config import get_settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import planned_workout
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.planned_workouts import push_planned_workout
from perseverer.staleness import check_staleness, notify_webhook

logger = logging.getLogger("perseverer.worker")


def run_daily_sync() -> None:
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        logger.info("starting scheduled garmin_connect sync")
        summary = sync_garmin_connect(
            conn,
            settings.raw_archive_dir,
            settings.parquet_dir,
            settings.garmin_tokenstore_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            rolling_window_days=settings.garmin_rolling_window_days,
            rate_limits=RateLimitSettings(
                request_interval_s=settings.garmin_request_interval_s,
                max_requests_per_hour=settings.garmin_max_requests_per_hour,
            ),
        )
        logger.info(
            "garmin_connect sync finished: seen=%d new=%d errors=%d",
            summary.items_seen,
            summary.items_new,
            len(summary.errors),
        )

        # Separately try/excepted -- a Eufy failure (e.g. bad credentials, API change) must
        # never block the Garmin sync or the staleness check that follows.
        try:
            logger.info("starting scheduled eufy sync")
            eufy_summary = sync_eufy(
                conn,
                settings.raw_archive_dir,
                settings.parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID,
                email=settings.eufy_email,
                password=settings.eufy_password,
                device_id=settings.eufy_device_id,
                customer_id=settings.eufy_customer_id,
            )
            logger.info(
                "eufy sync finished: seen=%d new=%d errors=%d",
                eufy_summary.items_seen,
                eufy_summary.items_new,
                len(eufy_summary.errors),
            )
        except Exception:
            logger.exception("eufy sync failed unexpectedly")

        alerts = check_staleness(
            conn,
            DEFAULT_ATHLETE_ID,
            escalate_after_days=settings.garmin_stale_escalate_days,
            freshness_days=settings.export_freshness_days,
        )
        for alert in alerts:
            logger.warning(
                "staleness alert [%s/%s]: %s", alert.source, alert.severity, alert.message
            )
            if settings.staleness_webhook_url:
                notify_webhook(settings.staleness_webhook_url, alert)


def run_daily_backup() -> None:
    """Separate scheduled job (its own hour/minute, see main() below), not folded into
    run_daily_sync -- a backup is independent of whether the Garmin/Eufy sync above succeeded or
    failed, and giving it its own cron time keeps it off the sync's own I/O-heavy window."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    logger.info("starting scheduled backup")
    try:
        result = create_backup(
            engine,
            settings.raw_archive_dir,
            settings.parquet_dir,
            settings.backups_dir,
            backup_user=settings.backup_user,
            backup_host=settings.backup_host,
            backup_path=settings.backup_path,
            ssh_key_path=settings.backup_ssh_key_path,
            known_hosts_path=str(settings.backup_known_hosts_path),
            keep_local_snapshots=settings.backup_keep_local_snapshots,
        )
    except Exception:
        logger.exception("scheduled backup failed unexpectedly")
        return
    if result is None:
        logger.info("scheduled backup skipped: not configured")
    else:
        logger.info(
            "scheduled backup finished: snapshot=%s destination=%s pruned_local=%d",
            result.snapshot_path.name,
            result.destination,
            result.pruned_local_snapshots,
        )


def run_daily_workout_push() -> None:
    """Separate scheduled job (its own hour/minute, see main() below), right after the daily
    sync -- automatically pushes every `planned_workout` due within the coming
    `planned_workout_push_window_days` days (default 7, "push it if it's within the coming
    week" -- the athlete's own choice, see docs/adr/0015-scheduled-workouts.md) that hasn't been
    pushed yet (`push_status != "pushed"`). `push_planned_workout` already catches and records
    every per-workout failure itself (`push_status="push_failed"` + `push_error`) -- see its own
    docstring -- so this loop only needs to handle the one exception it deliberately lets
    through: `GarminRateLimitAborted`, which means the whole session hit Garmin's rate limit, not
    that any specific workout is broken, and stops this run entirely (same "no retry, let the
    next scheduled run continue" precedent as `run_daily_sync`)."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    today = datetime.now(UTC).date()
    window_end = today + timedelta(days=settings.planned_workout_push_window_days)

    rate_limits = RateLimitSettings(
        request_interval_s=settings.garmin_request_interval_s,
        max_requests_per_hour=settings.garmin_max_requests_per_hour,
    )

    with engine.connect() as conn:
        due = conn.execute(
            select(planned_workout.c.id, planned_workout.c.local_date).where(
                planned_workout.c.athlete_id == DEFAULT_ATHLETE_ID,
                planned_workout.c.push_status != "pushed",
                planned_workout.c.local_date >= today.isoformat(),
                planned_workout.c.local_date <= window_end.isoformat(),
            )
        ).fetchall()

        logger.info("starting scheduled workout push: %d workout(s) due", len(due))
        pushed = 0
        for row in due:
            try:
                result = push_planned_workout(
                    conn,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    planned_workout_id=row.id,
                    tokenstore_dir=settings.garmin_tokenstore_dir,
                    rate_limits=rate_limits,
                )
                if result.success:
                    pushed += 1
                else:
                    logger.warning(
                        "workout push failed for planned_workout %s (%s): %s",
                        row.id,
                        row.local_date,
                        result.error,
                    )
            except GarminRateLimitAborted as e:
                logger.warning("scheduled workout push run aborted by rate limit: %s", e)
                break
        logger.info("scheduled workout push finished: pushed=%d of %d due", pushed, len(due))


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("worker started (environment=%s)", settings.environment)

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_daily_sync,
        trigger=CronTrigger(
            hour=settings.schedule_hour,
            minute=settings.schedule_minute,
            jitter=settings.schedule_jitter_s,
            timezone=settings.schedule_timezone,
        ),
        id="daily_garmin_sync",
    )
    logger.info(
        "scheduled daily sync at %02d:%02d %s (+/- %ds jitter)",
        settings.schedule_hour,
        settings.schedule_minute,
        settings.schedule_timezone,
        settings.schedule_jitter_s,
    )
    scheduler.add_job(
        run_daily_backup,
        trigger=CronTrigger(
            hour=settings.backup_schedule_hour,
            minute=settings.backup_schedule_minute,
            timezone=settings.schedule_timezone,
        ),
        id="daily_backup",
    )
    logger.info(
        "scheduled daily backup at %02d:%02d %s",
        settings.backup_schedule_hour,
        settings.backup_schedule_minute,
        settings.schedule_timezone,
    )
    scheduler.add_job(
        run_daily_workout_push,
        trigger=CronTrigger(
            hour=settings.workout_push_schedule_hour,
            minute=settings.workout_push_schedule_minute,
            timezone=settings.schedule_timezone,
        ),
        id="daily_workout_push",
    )
    logger.info(
        "scheduled daily workout push at %02d:%02d %s (window=%d days)",
        settings.workout_push_schedule_hour,
        settings.workout_push_schedule_minute,
        settings.schedule_timezone,
        settings.planned_workout_push_window_days,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
