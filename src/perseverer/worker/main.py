"""Worker container entrypoint: runs the daily garmin_connect sync + staleness check on a
cron schedule (default 04:15 UTC, jittered — see §6 of the project spec). The schedule resolves
against `schedule_timezone` (an IANA name, default UTC), not the container's own system clock --
see that setting's own docstring in config.py for why a real timezone rather than a fixed hour
offset matters here (DST).

`garmin_export` is deliberately never scheduled here — it's a one-off/occasional CLI action
(`sync import garmin-export <path>`), not a recurring job.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from perseverer.adapters.eufy import sync_eufy
from perseverer.adapters.garmin_connect import RateLimitSettings, sync_garmin_connect
from perseverer.config import get_settings
from perseverer.db.engine import make_engine
from perseverer.db.seed import DEFAULT_ATHLETE_ID
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
    scheduler.start()


if __name__ == "__main__":
    main()
