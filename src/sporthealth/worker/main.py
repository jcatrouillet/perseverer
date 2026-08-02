"""Worker container entrypoint.

Phase 0 placeholder: keeps the container alive and logs that it's up. APScheduler and the
daily sync job (rolling re-fetch window, ingest_run bookkeeping, staleness webhook) land in
Phase 2 alongside the garmin_connect adapter — see §6 of the project spec.
"""

import logging
import time

from sporthealth.config import get_settings

logger = logging.getLogger("sporthealth.worker")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info(
        "worker skeleton started (environment=%s); no scheduled jobs yet", settings.environment
    )
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
