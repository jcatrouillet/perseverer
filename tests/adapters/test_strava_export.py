"""strava_export tests: activities.csv + real-shaped mixed file formats (see ADR 0012),
sport-map correctness, CSV-totals overlay, manual-entry (no-file) rows, idempotent re-import,
and -- the acceptance criterion this whole phase is measured against -- that a Strava-sourced
activity matching an existing activity from another source links into the *same* activity
rather than creating a duplicate."""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from perseverer.adapters.fit_folder import ingest_canonical_batch
from perseverer.adapters.strava_export import _map_sport, import_strava_export
from perseverer.archive import archive_raw_bytes
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_metric, activity_source_link, athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import CanonicalActivity, CanonicalBatch


def _mock_weather_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests aren't about weather at all -- a failed response makes
    backfill_weather_titles (wired into every ingest entry point, including this one, now that
    it runs automatically) a clean no-op for this fixture's real GPS coordinates, instead of
    silently reaching real Open-Meteo over the network."""
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda r: httpx.Response(503))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport))


_GPX_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="StravaGPX" version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
 <trk>
  <trkseg>
   <trkpt lat="37.6656890" lon="-112.1102740">
    <ele>2082.6</ele><time>2026-04-17T22:17:09Z</time>
   </trkpt>
   <trkpt lat="37.6657130" lon="-112.1102920">
    <ele>2085.0</ele><time>2026-04-17T22:47:09Z</time>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""

_CSV_HEADER = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,"
    "Moving Time,Elevation Gain,Calories,Relative Effort,Filename"
)


def _csv_row(
    activity_id: str,
    *,
    activity_type: str,
    name: str,
    date_text: str,
    elapsed: str,
    distance_m: str,
    filename: str,
) -> str:
    return (
        f"{activity_id},{date_text},{name},{activity_type},{elapsed},{distance_m},"
        f"{elapsed},50,300,10,{filename}"
    )


def _seed_athlete(engine: object) -> None:
    with engine.connect() as conn:  # type: ignore[attr-defined]
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


def _make_archive(tmp_path: Path, *, with_manual_row: bool = True) -> Path:
    root = tmp_path / "strava"
    (root / "activities").mkdir(parents=True)
    (root / "activities" / "999111.gpx").write_bytes(_GPX_BODY)

    rows = [
        _csv_row(
            "999111",
            activity_type="Hike",
            name="Bryce Canyon hike",
            date_text='"Apr 17, 2026, 10:17:09 PM"',
            elapsed="1800",
            distance_m="4800.5",
            filename="activities/999111.gpx",
        )
    ]
    if with_manual_row:
        rows.append(
            _csv_row(
                "999222",
                activity_type="Weight Training",
                name="Gym session",
                date_text='"Apr 18, 2026, 6:00:00 AM"',
                elapsed="2400",
                distance_m="0",
                filename="",
            )
        )
    (root / "activities.csv").write_text(_CSV_HEADER + "\n" + "\n".join(rows) + "\n")
    return root


def test_sport_map_matches_real_verified_fit_derived_values() -> None:
    # Verified by decoding real FIT files of each Strava CSV type from a real export archive
    # (see ADR 0012) -- not guessed.
    assert _map_sport("Run") == ("running", "generic")
    assert _map_sport("Hike") == ("hiking", "generic")
    assert _map_sport("Weight Training") == ("training", "strength_training")
    assert _map_sport("Yoga") == ("training", "yoga")
    # Unmapped falls back to a lowercased, underscored version of the raw type, not "unknown".
    assert _map_sport("Kayaking") == ("kayaking", None)


def test_gpx_activity_gets_csv_totals_overlaid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_weather_unavailable(monkeypatch)
    root = _make_archive(tmp_path, with_manual_row=False)
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = import_strava_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        row = conn.execute(select(activity)).fetchone()

    assert summary.items_seen == 1
    assert summary.items_new == 1
    assert summary.errors == []
    assert row is not None
    assert row.sport == "hiking"
    assert row.sub_sport == "generic"
    assert row.name == "Bryce Canyon hike"
    assert row.distance_m == 4800.5  # from CSV, not re-derived from the two GPS points
    assert row.duration_s == 1800.0


def test_ingest_automatically_prepends_a_weather_emoji_to_the_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive counterpart to the (mocked-unavailable) tests above -- proves
    backfill_weather_titles is actually wired into import_strava_export's own touched-dates
    block, not just that its absence doesn't break anything. See weather_titles.py's own
    docstring for why this now runs automatically on every ingest, not just via the CLI."""
    real_client = httpx.Client
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-04-17T22:00"],
                    "temperature_2m": [15.0],
                    "relative_humidity_2m": [40.0],
                    "weathercode": [0],  # clear sky -> ☀️
                    "apparent_temperature": [14.0],
                    "wind_speed_10m": [2.0],
                    "wind_direction_10m": [180.0],
                }
            },
        )
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport))

    root = _make_archive(tmp_path, with_manual_row=False)
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        import_strava_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        row = conn.execute(select(activity.c.name)).scalar_one()

    assert row == "☀️ Bryce Canyon hike"


def test_gpx_activity_gets_hr_and_elevation_loss_from_csv(tmp_path: Path) -> None:
    # GPX carries no session-level avg/max HR or elevation-loss field of its own -- these must
    # come from activities.csv's own real Average/Max Heart Rate and Elevation Loss columns
    # (ADR 0013), registered under source-honest strava.session.* keys, not fit.session.*.
    root = tmp_path / "strava"
    (root / "activities").mkdir(parents=True)
    (root / "activities" / "999333.gpx").write_bytes(_GPX_BODY)
    header = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,"
        "Moving Time,Elevation Gain,Elevation Loss,Max Heart Rate,Average Heart Rate,"
        "Calories,Relative Effort,Filename"
    )
    row = (
        '999333,"Apr 17, 2026, 10:17:09 PM",Bryce Canyon hike,Hike,1800,4800.5,1800,'
        "120,45,168,142,300,10,activities/999333.gpx"
    )
    (root / "activities.csv").write_text(header + "\n" + row + "\n")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        import_strava_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        act = conn.execute(select(activity.c.id)).scalar_one()
        metrics = {
            m.metric_key: m.value_num
            for m in conn.execute(
                select(activity_metric).where(activity_metric.c.activity_id == act)
            ).fetchall()
        }

    assert metrics["strava.session.avg_heart_rate"] == 142.0
    assert metrics["strava.session.max_heart_rate"] == 168.0
    assert metrics["strava.session.total_descent"] == 45.0


def test_manual_entry_row_with_no_file_still_creates_an_activity(tmp_path: Path) -> None:
    root = _make_archive(tmp_path, with_manual_row=True)
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = import_strava_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        rows = conn.execute(select(activity.c.sport, activity.c.name)).fetchall()

    assert summary.items_seen == 2
    assert summary.items_new == 2
    assert ("training", "Gym session") in [(r.sport, r.name) for r in rows]


def test_reimport_is_idempotent(tmp_path: Path) -> None:
    root = _make_archive(tmp_path, with_manual_row=True)
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        import_strava_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        second = import_strava_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        activity_count = conn.execute(select(activity.c.id)).fetchall()

    assert second.items_new == 0
    assert len(activity_count) == 2  # not 4 -- the second run is a clean no-op


def test_gz_file_is_decompressed_and_archived_verbatim_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_weather_unavailable(monkeypatch)
    root = tmp_path / "strava"
    (root / "activities").mkdir(parents=True)
    (root / "activities" / "999333.gpx.gz").write_bytes(gzip.compress(_GPX_BODY))
    row = _csv_row(
        "999333",
        activity_type="Hike",
        name="Gzipped hike",
        date_text='"Apr 17, 2026, 10:17:09 PM"',
        elapsed="1800",
        distance_m="4800.5",
        filename="activities/999333.gpx.gz",
    )
    (root / "activities.csv").write_text(_CSV_HEADER + "\n" + row + "\n")

    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        summary = import_strava_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )
        row_out = conn.execute(select(activity.c.name)).scalar_one()

    assert summary.errors == []
    assert row_out == "Gzipped hike"


def test_strava_activity_merges_into_existing_activity_from_another_source(
    tmp_path: Path,
) -> None:
    """The literal acceptance criterion: an activity present in both sources shows one merged
    record with both sources inspectable -- not a duplicate."""
    root = _make_archive(tmp_path, with_manual_row=False)
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)

    with engine.connect() as conn:
        # Seed a "fit_folder"-sourced activity that's the same real-world hike: same start
        # time, same sport family, a duration within the merge engine's threshold.
        seeded = CanonicalActivity(
            start_time_utc=dt.datetime(2026, 4, 17, 22, 17, 9),
            utc_offset_s=0,
            sport="hiking",
            sub_sport="generic",
            name="Bryce Canyon Hike (Garmin)",
            duration_s=1795.0,  # within the 60s/5% merge threshold of the Strava row's 1800s
            moving_duration_s=None,
            distance_m=4800.0,
            elevation_gain_m=None,
            max_altitude_m=None,
            calories=None,
            device=None,
        )
        seed_raw_id = archive_raw_bytes(
            conn,
            tmp_path / "archive",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            kind="fit",
            content=b"seed-fit-bytes",
        )
        ingest_canonical_batch(
            conn,
            tmp_path / "parquet",
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            raw_object_id=seed_raw_id,
            sha256="seed-sha",
            batch=CanonicalBatch(kind="activity", activity=seeded),
        )
        conn.commit()

        import_strava_export(
            conn,
            tmp_path / "archive",
            tmp_path / "parquet",
            tmp_path / "extract",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=root,
        )

        activities = conn.execute(select(activity.c.id, activity.c.name)).fetchall()
        links = conn.execute(
            select(activity_source_link.c.source, activity_source_link.c.activity_id)
        ).fetchall()

    assert len(activities) == 1  # merged, not a second row
    assert activities[0].name == "Bryce Canyon Hike (Garmin)"  # first-source-wins, unchanged
    sources = {link.source for link in links}
    assert sources == {"fit_folder", "strava_export"}
    assert len({link.activity_id for link in links}) == 1  # both link rows point at the same id
