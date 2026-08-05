"""One-off backfill: recompute activity.local_date as offset-adjusted (start_time_utc +
utc_offset_s), not the raw UTC date -- see ADR 0009 decision 8. utc_offset_s is already
correctly stored per activity, so this is a pure SQL recompute, no FIT re-parsing needed.
Then refreshes every rollup table for every affected day/week/month, plus a full
fitness_daily_rollup recompute (already whole-history by design).

Run once against the real data/sporthealth.db; the ingest code fix (adapters/fit_folder.py)
covers all future ingests going forward.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select, text

from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.fitness import refresh_fitness_rollup
from sporthealth.rollups import month_start, refresh_daily_rollup, refresh_period_rollup, week_start_monday

DB_PATH = Path(__file__).parent.parent / "data" / "sporthealth.db"


def main() -> None:
    engine = make_engine(DB_PATH)
    with engine.connect() as conn:
        before = {
            row.id: row.local_date
            for row in conn.execute(select(activity.c.id, activity.c.local_date))
        }

        conn.execute(
            text(
                "UPDATE activity SET local_date = date(start_time_utc, utc_offset_s || ' seconds')"
            )
        )
        conn.commit()

        after = {
            row.id: row.local_date
            for row in conn.execute(select(activity.c.id, activity.c.local_date))
        }

        changed_dates: set[str] = set()
        for activity_id, old_date in before.items():
            new_date = after[activity_id]
            if new_date != old_date:
                changed_dates.add(old_date)
                changed_dates.add(new_date)

        print(f"{len(changed_dates)} distinct local_dates touched by the local_date change")

        for local_date in sorted(changed_dates):
            refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date=local_date)

        periods: set[tuple[str, str]] = set()
        for local_date in changed_dates:
            periods.add(("week", week_start_monday(local_date)))
            periods.add(("month", month_start(local_date)))

        for period_type, period_start in sorted(periods):
            refresh_period_rollup(
                conn, athlete_id=DEFAULT_ATHLETE_ID, period_type=period_type, period_start=period_start
            )

        refresh_fitness_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        print(f"refreshed {len(changed_dates)} day_rollup rows, {len(periods)} period_rollup rows")


if __name__ == "__main__":
    main()
