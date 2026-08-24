"""One-off backfill: compute activity.max_altitude_m for every activity ingested before this
column existed. Read straight from each activity's own already-written Parquet file (its
altitude_m channel, when present) rather than re-parsing raw bytes -- the same value the FIT/GPX/
TCX parsers now compute at ingest time, just derived from the stream that's already on disk.

No rollup cascade needed: max_altitude_m isn't part of any *_rollup table, only a plain activity
column read directly by HikeStatsCard's own client-side reduce and ActivityStatsGrid's Elevation
section.

Run once against the real data/perseverer.db; the ingest code fix (fit/parser.py, gpx/parser.py,
tcx/parser.py) covers all future ingests going forward.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import duckdb
from sqlalchemy import select

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_stream

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "perseverer.db"
PARQUET_DIR = DATA_DIR / "parquet"


def main() -> None:
    engine = make_engine(DB_PATH)
    # Plain in-memory DuckDB, just to read Parquet files directly -- no SQLite attach needed
    # (unlike api/duckdb_conn.py's make_duckdb_connection, which exists for querying the app's
    # own SQLite tables through DuckDB), so no "sqlite" extension install/load either.
    con = duckdb.connect(":memory:")
    with engine.connect() as conn:
        candidates = conn.execute(
            select(activity.c.id, activity_stream.c.parquet_path, activity_stream.c.channels)
            .select_from(
                activity.join(activity_stream, activity_stream.c.activity_id == activity.c.id)
            )
            .where(activity.c.max_altitude_m.is_(None))
        ).fetchall()

        print(f"{len(candidates)} activities with a stream and no max_altitude_m yet")

        updated = 0
        skipped_no_altitude = 0
        for row in candidates:
            # Not every device records altitude (see activity_stream.channels, the same
            # ingest-time-written, trusted list activity_trim.py's own recompute already checks
            # before selecting a channel) -- a plain "SELECT altitude_m" against a Parquet file
            # that never had that column errors instead of returning NULL.
            if "altitude_m" not in json.loads(row.channels):
                skipped_no_altitude += 1
                continue
            parquet_path = PARQUET_DIR / row.parquet_path
            if not parquet_path.exists():
                print(f"  skipping {row.id}: {parquet_path} not found")
                continue
            result = con.execute(
                "SELECT max(altitude_m) FROM read_parquet(?)", [str(parquet_path)]
            ).fetchone()
            max_altitude_m = result[0] if result is not None else None
            if max_altitude_m is None:
                skipped_no_altitude += 1
                continue
            conn.execute(
                activity.update()
                .where(activity.c.id == row.id)
                .values(max_altitude_m=max_altitude_m)
            )
            updated += 1
        conn.commit()

        print(
            f"updated {updated} activities, "
            f"{skipped_no_altitude} had no altitude_m channel data at all"
        )


if __name__ == "__main__":
    main()
