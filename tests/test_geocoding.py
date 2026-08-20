"""Tests for geocoding.py: the pure Nominatim response parser, and the read-through-cache
fetch/archive/store orchestration (mocked HTTP, no real network calls). See CLAUDE.md's
"raw first, always" rule -- every fetched response must be archived before being parsed, and
re-looking-up the same activity's location must never happen once it's cached.

The two real response shapes below (Sunnyvale city, and a nature-reserve/national-park hit) were
confirmed via live calls to Nominatim during development, not assumed -- see geocoding.py's own
module docstring.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path

import httpx
from sqlalchemy import Connection, Engine, select

from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity, activity_metric, athlete, metadata, raw_object
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.geocoding import (
    METRIC_LOCATION_NAME,
    get_or_fetch_activity_location,
    parse_nominatim_response,
)

_SUNNYVALE_RESPONSE = {
    "place_id": 322117143,
    "osm_type": "way",
    "osm_id": 28415566,
    "lat": "37.4124156",
    "lon": "-121.9990259",
    "category": "highway",
    "type": "service",
    "addresstype": "road",
    "name": "Baylands Park",
    "display_name": (
        "Baylands Park, Sunnyvale, Santa Clara County, California, 94089, United States"
    ),
    "address": {
        "road": "Baylands Park",
        "city": "Sunnyvale",
        "county": "Santa Clara County",
        "state": "California",
        "postcode": "94089",
        "country": "United States",
        "country_code": "us",
    },
}

_YOSEMITE_RESPONSE = {
    "place_id": 320659322,
    "osm_type": "relation",
    "osm_id": 1643367,
    "lat": "37.8393004",
    "lon": "-119.5164635",
    "category": "leisure",
    "type": "nature_reserve",
    "addresstype": "nature_reserve",
    "name": "Yosemite National Park",
    "display_name": "Yosemite National Park, California, United States",
}


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


def _seed_activity(conn: Connection, activity_id: str, start: dt.datetime) -> None:
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=0,
            local_date=start.date().isoformat(),
            sport="running",
            duration_s=3600.0,
            distance_m=10000.0,
            primary_source="fit_folder",
            created_at=start,
            updated_at=start,
        )
    )
    conn.commit()


class TestParseNominatimResponse:
    def test_city_from_address_breakdown(self) -> None:
        assert parse_nominatim_response(_SUNNYVALE_RESPONSE) == "Sunnyvale, California"

    def test_national_park_when_the_closest_feature_is_tagged_as_one(self) -> None:
        assert parse_nominatim_response(_YOSEMITE_RESPONSE) == "Yosemite National Park"

    def test_falls_back_through_town_village_hamlet_county(self) -> None:
        # Real shape confirmed live at Old Faithful, Yellowstone: no city/town/village, only a
        # hamlet -- with a state present, so it's used as the suffix, same as a real city would.
        raw = {
            "address": {
                "hamlet": "Upper Geyser Basin",
                "county": "Teton County",
                "state": "Wyoming",
            }
        }
        assert parse_nominatim_response(raw) == "Upper Geyser Basin, Wyoming"

    def test_place_alone_when_no_state_or_country_available(self) -> None:
        raw = {"address": {"county": "Mariposa County"}}
        assert parse_nominatim_response(raw) == "Mariposa County"

    def test_national_park_key_in_address_wins_over_city(self) -> None:
        raw = {"address": {"national_park": "Yellowstone National Park", "city": "Somewhere"}}
        assert parse_nominatim_response(raw) == "Yellowstone National Park"

    def test_country_only_when_no_state(self) -> None:
        raw = {"address": {"city": "Tokyo", "country": "Japan"}}
        assert parse_nominatim_response(raw) == "Tokyo, Japan"

    def test_none_when_address_has_no_usable_place_name(self) -> None:
        assert parse_nominatim_response({"address": {"postcode": "12345"}}) is None

    def test_none_when_address_block_is_missing(self) -> None:
        assert parse_nominatim_response({}) is None


class TestGetOrFetchActivityLocation:
    def test_fetches_archives_and_stores_on_a_cache_miss(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_SUNNYVALE_RESPONSE)

        mock_client = httpx.Client(transport=httpx.MockTransport(handler))
        archive_root = tmp_path / "raw"

        with engine.connect() as conn:
            location = get_or_fetch_activity_location(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                lat=37.4124, lon=-121.9990, client=mock_client,
            )
            conn.commit()

        assert request_count == 1
        assert location == "Sunnyvale, California"

        with engine.connect() as conn:
            raw_rows = conn.execute(
                select(raw_object.c.storage_path, raw_object.c.kind, raw_object.c.source).where(
                    raw_object.c.source == "nominatim"
                )
            ).fetchall()
            assert len(raw_rows) == 1
            assert raw_rows[0].kind == "nominatim_reverse_json"
            blob = gzip.decompress((archive_root / raw_rows[0].storage_path).read_bytes())
            assert json.loads(blob) == _SUNNYVALE_RESPONSE

    def test_second_call_reads_the_cache_and_makes_no_network_request(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_SUNNYVALE_RESPONSE)

        archive_root = tmp_path / "raw"
        for _ in range(2):
            mock_client = httpx.Client(transport=httpx.MockTransport(handler))
            with engine.connect() as conn:
                location = get_or_fetch_activity_location(
                    conn, archive_root,
                    athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                    lat=37.4124, lon=-121.9990, client=mock_client,
                )
                conn.commit()

        assert request_count == 1  # only the first call ever hit the network
        assert location == "Sunnyvale, California"

    def test_returns_none_and_writes_nothing_on_a_network_failure(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        mock_client = httpx.Client(transport=httpx.MockTransport(handler))
        archive_root = tmp_path / "raw"

        with engine.connect() as conn:
            location = get_or_fetch_activity_location(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                lat=37.4124, lon=-121.9990, client=mock_client,
            )
            conn.commit()

        assert location is None
        with engine.connect() as conn:
            written = conn.execute(
                select(raw_object).where(raw_object.c.source == "nominatim")
            ).fetchall()
            assert written == []

    def test_returns_none_and_writes_nothing_when_response_has_no_usable_name(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"address": {"postcode": "12345"}})
        )
        mock_client = httpx.Client(transport=transport)
        archive_root = tmp_path / "raw"

        with engine.connect() as conn:
            location = get_or_fetch_activity_location(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                lat=37.4124, lon=-121.9990, client=mock_client,
            )
            conn.commit()

        assert location is None
        # The response IS archived (raw-first, always) even though it produced no usable name --
        # only the parsed activity_metric row is skipped.
        with engine.connect() as conn:
            raw_rows = conn.execute(
                select(raw_object).where(raw_object.c.source == "nominatim")
            ).fetchall()
            assert len(raw_rows) == 1
            metric_rows = conn.execute(
                select(activity_metric).where(activity_metric.c.source == "nominatim")
            ).fetchall()
            assert metric_rows == []

    def test_stores_the_location_name_metric_key(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        start = dt.datetime(2026, 8, 2, 8, 20, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity(conn, "a1", start)

        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_YOSEMITE_RESPONSE))
        mock_client = httpx.Client(transport=transport)
        archive_root = tmp_path / "raw"

        with engine.connect() as conn:
            get_or_fetch_activity_location(
                conn, archive_root,
                athlete_id=DEFAULT_ATHLETE_ID, activity_id="a1",
                lat=37.8393, lon=-119.5164, client=mock_client,
            )
            conn.commit()

        with engine.connect() as conn:
            row = conn.execute(
                select(activity_metric.c.metric_key, activity_metric.c.value_text).where(
                    activity_metric.c.activity_id == "a1", activity_metric.c.source == "nominatim"
                )
            ).one()
        assert row.metric_key == METRIC_LOCATION_NAME
        assert row.value_text == "Yosemite National Park"
