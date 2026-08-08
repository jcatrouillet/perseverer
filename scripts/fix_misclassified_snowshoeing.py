"""One-off correction: 2 winter activities were recorded with FIT session.sport = "hiking"
even though they were actually snowshoeing -- the athlete directly confirmed all 4 snowshoeing
outings for 2026 (2026-01-31, 2026-02-01, 2026-03-07, 2026-03-08); the DB only had 2 of those 4
under sport='snowshoeing' (03-07 and 03-08), with the other two (01-31, 02-01) sitting under
sport='hiking'. Unlike the Bryce Canyon hikes correction (fix_misclassified_hikes.py), there's
no independent raw-archive signal here (no descriptive filename, no duration outlier) -- the
athlete's own confirmation is the ground truth. Likely cause: whatever recorded 01-31/02-01
didn't offer a distinct "snowshoeing" activity profile the way the device used on 03-07/03-08
did, so it fell back to the closest available profile (hiking).

This corrects the parsed `activity.sport` column only; the raw archived FIT bytes are never
touched. Then refreshes every rollup depending on the corrected dates.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select, update

from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.fitness import refresh_fitness_rollup
from sporthealth.rollups import (
    month_start,
    refresh_daily_rollup,
    refresh_period_rollup,
    week_start_monday,
)

DB_PATH = Path(__file__).parent.parent / "data" / "sporthealth.db"

MISCLASSIFIED_SNOWSHOEING_IDS = [
    "01KZ5ZSG4YQRMVX6MJ2CT7QX3J",  # 2026-01-31, confirmed snowshoeing by the athlete
    "01KZ5ZSQMXDW3ADTPMDSX9NDME",  # 2026-02-01, confirmed snowshoeing by the athlete
]


def main() -> None:
    engine = make_engine(DB_PATH)
    with engine.connect() as conn:
        rows = conn.execute(
            select(activity.c.id, activity.c.local_date, activity.c.sport).where(
                activity.c.id.in_(MISCLASSIFIED_SNOWSHOEING_IDS)
            )
        ).fetchall()
        found_ids = {r.id for r in rows}
        missing = set(MISCLASSIFIED_SNOWSHOEING_IDS) - found_ids
        if missing:
            raise SystemExit(f"activity id(s) not found, aborting: {missing}")

        changed_dates = {r.local_date for r in rows}

        conn.execute(
            update(activity)
            .where(activity.c.id.in_(MISCLASSIFIED_SNOWSHOEING_IDS))
            .values(sport="snowshoeing")
        )
        conn.commit()

        for local_date in sorted(changed_dates):
            refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date=local_date)

        periods: set[tuple[str, str]] = set()
        for local_date in changed_dates:
            periods.add(("week", week_start_monday(local_date)))
            periods.add(("month", month_start(local_date)))
        for period_type, period_start in sorted(periods):
            refresh_period_rollup(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type=period_type,
                period_start=period_start,
            )

        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        print(f"corrected {len(found_ids)} activities to sport='snowshoeing'")
        print(f"refreshed {len(changed_dates)} day_rollup rows, {len(periods)} period_rollup rows")


if __name__ == "__main__":
    main()
