"""One-off correction: 6 activities were re-exported through a third-party tool ("Sauce for
Strava", see fit.file_id.product_name) that always writes FIT session.sport = "running"
regardless of the real activity type -- confirmed by decoding the raw archived FIT bytes
directly (not assumed). Of those 6, 5 are hikes, identified from independent evidence in the
same raw archive:

- 4 have explicit hike/trail filenames in raw_object.source_locator (Sands_Cave_Hike,
  Bryce_Canyon_-_Bristlecone_trail, Bryce_Canyon_-_Queens_garden_+_Navajo_trails,
  Bryce_Canyon_-_Mossy_cave).
- 1 (2022-06-07) has no descriptive filename, but 29.4km with 8.9h of *moving* time (~18
  min/km) is decisively a hike, not a run, on duration alone.
- The 6th ("Evening_Run", 5.2km at a normal ~6.4 min/km pace) is a genuine run from the same
  tool and is left untouched -- the tool's sport field is unreliable, not always wrong.

This corrects the parsed `activity.sport` column only; the raw archived FIT bytes are never
touched (they still say "running", which is what the source tool actually wrote -- raw stays
raw). Then refreshes every rollup depending on the corrected dates.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select, update

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fitness import refresh_fitness_rollup
from perseverer.rollups import (
    month_start,
    refresh_daily_rollup,
    refresh_period_rollup,
    week_start_monday,
)

DB_PATH = Path(__file__).parent.parent / "data" / "perseverer.db"

MISCLASSIFIED_HIKE_IDS = [
    "01KZ5TFAN5AV6CHJWXT50PAY01",  # 2022-06-07, 29.4km, 8.9h moving -- ~18 min/km
    "01KZ60B81PZ7XKMEGZN5CP1T0W",  # 2026-04-16, Sands_Cave_Hike.fit
    "01KZ60B84V3VGKB8QM0N67ZFC3",  # 2026-04-17, Bryce_Canyon_-_Bristlecone_trail.fit
    "01KZ60B8DPWGVRD4CA6MMPGR4J",  # 2026-04-17, Bryce_Canyon_-_Queens_garden_+_Navajo_trails.fit
    "01KZ60B8H55GBS5JXW0JBT05ZM",  # 2026-04-17, Bryce_Canyon_-_Mossy_cave.fit
]


def main() -> None:
    engine = make_engine(DB_PATH)
    with engine.connect() as conn:
        rows = conn.execute(
            select(activity.c.id, activity.c.local_date, activity.c.sport).where(
                activity.c.id.in_(MISCLASSIFIED_HIKE_IDS)
            )
        ).fetchall()
        found_ids = {r.id for r in rows}
        missing = set(MISCLASSIFIED_HIKE_IDS) - found_ids
        if missing:
            raise SystemExit(f"activity id(s) not found, aborting: {missing}")

        changed_dates = {r.local_date for r in rows}

        conn.execute(
            update(activity).where(activity.c.id.in_(MISCLASSIFIED_HIKE_IDS)).values(sport="hiking")
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

        print(f"corrected {len(found_ids)} activities to sport='hiking'")
        print(f"refreshed {len(changed_dates)} day_rollup rows, {len(periods)} period_rollup rows")


if __name__ == "__main__":
    main()
