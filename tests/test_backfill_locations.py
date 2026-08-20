"""Tests for backfill_locations.py -- the coordinate-rounding dedup logic is the interesting
part here (test_geocoding.py already exhaustively covers the underlying fetch/archive/store
mechanics against a mocked HTTP transport)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, select

import sporthealth.geocoding as geocoding_module
from sporthealth.backfill_locations import backfill_locations
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity, activity_metric, athlete, metadata, route_geom
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.geocoding import METRIC_LOCATION_NAME, SOURCE, _store

_SUNNYVALE_RESPONSE = {
    "addresstype": "road",
    "name": "Baylands Park",
    "address": {"city": "Sunnyvale", "state": "California", "country_code": "us"},
}
_DENVER_RESPONSE = {
    "addresstype": "road",
    "name": "Main St",
    "address": {"city": "Denver", "state": "Colorado", "country_code": "us"},
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


def _seed(
    engine: Engine, *, activity_id: str, start: dt.datetime, lat: float | None, lon: float | None
) -> None:
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=start,
                utc_offset_s=0,
                local_date=start.date().isoformat(),
                sport="running",
                duration_s=1800.0,
                distance_m=5000.0,
                primary_source="fit_folder",
                created_at=start,
                updated_at=start,
            )
        )
        if lat is not None and lon is not None:
            conn.execute(
                route_geom.insert().values(
                    activity_id=activity_id,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    start_lat=lat,
                    start_lng=lon,
                    end_lat=lat,
                    end_lng=lon,
                    min_lat=lat,
                    min_lng=lon,
                    max_lat=lat,
                    max_lng=lon,
                )
            )
        conn.commit()


_RealHttpxClient = httpx.Client  # captured before any monkeypatching touches the shared module


def _mock_httpx_client(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    """backfill_locations calls get_or_fetch_activity_location with no `client` kwarg, so it
    constructs (and, being the owner, closes) its own httpx.Client internally -- patching the
    httpx.Client name inside geocoding's own module namespace redirects that construction to a
    mock transport. A fresh instance per call, matching the real construct-then-close lifecycle
    the code under test drives: reusing one instance across calls breaks the second call, since
    the first call closes it once done. Must build from `_RealHttpxClient`, not `httpx.Client`
    -- `geocoding_module.httpx` *is* the same `httpx` package object this file imports, so
    patching one name patches both, and calling the patched name here would recurse forever."""
    monkeypatch.setattr(
        geocoding_module.httpx,  # type: ignore[attr-defined]
        "Client",
        lambda *a, **k: _RealHttpxClient(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


def test_activities_starting_at_the_same_rounded_coordinate_share_one_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    start = dt.datetime(2026, 6, 1, 8, 0, 0, tzinfo=dt.UTC)
    # Same front-door start point, three separate runs -- all three round to the identical
    # (37.362, -121.974) group at 3 decimal places (~111m); chosen well clear of any x.xxx5
    # rounding boundary so a tiny perturbation can't accidentally land in a different bucket.
    _seed(engine, activity_id="a1", start=start, lat=37.36200, lon=-121.97400)
    _seed(
        engine, activity_id="a2", start=start + dt.timedelta(days=1), lat=37.36201, lon=-121.97401
    )
    _seed(
        engine, activity_id="a3", start=start + dt.timedelta(days=2), lat=37.36199, lon=-121.97399
    )

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=_SUNNYVALE_RESPONSE)

    _mock_httpx_client(monkeypatch, handler)

    with engine.connect() as conn:
        backfilled = backfill_locations(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

    assert request_count == 1  # only the first activity in the group ever hit the network
    assert backfilled == 3

    with engine.connect() as conn:
        rows = conn.execute(
            select(activity_metric.c.activity_id, activity_metric.c.value_text).where(
                activity_metric.c.source == SOURCE,
                activity_metric.c.metric_key == METRIC_LOCATION_NAME,
            )
        ).fetchall()
    by_activity = {r.activity_id: r.value_text for r in rows}
    assert by_activity == {
        "a1": "Sunnyvale, California",
        "a2": "Sunnyvale, California",
        "a3": "Sunnyvale, California",
    }


def test_activities_at_different_locations_each_get_their_own_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    start = dt.datetime(2026, 6, 1, 8, 0, 0, tzinfo=dt.UTC)
    _seed(engine, activity_id="a1", start=start, lat=37.3622, lon=-121.9745)  # Sunnyvale
    _seed(
        engine, activity_id="a2", start=start + dt.timedelta(days=1), lat=39.7392, lon=-104.9903
    )  # Denver

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if "37.362" in str(request.url):
            return httpx.Response(200, json=_SUNNYVALE_RESPONSE)
        return httpx.Response(200, json=_DENVER_RESPONSE)

    _mock_httpx_client(monkeypatch, handler)

    with engine.connect() as conn:
        backfilled = backfill_locations(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

    assert request_count == 2
    assert backfilled == 2


def test_activities_with_no_gps_are_skipped(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    start = dt.datetime(2026, 6, 1, 8, 0, 0, tzinfo=dt.UTC)
    _seed(engine, activity_id="a1", start=start, lat=None, lon=None)

    with engine.connect() as conn:
        backfilled = backfill_locations(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

    assert backfilled == 0


def test_already_cached_activities_are_skipped_without_a_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    start = dt.datetime(2026, 6, 1, 8, 0, 0, tzinfo=dt.UTC)
    _seed(engine, activity_id="a1", start=start, lat=37.3622, lon=-121.9745)

    with engine.connect() as conn:
        _store(conn, DEFAULT_ATHLETE_ID, "a1", "Already Cached")
        conn.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never hit the network for an already-cached activity")

    _mock_httpx_client(monkeypatch, handler)

    with engine.connect() as conn:
        backfilled = backfill_locations(conn, tmp_path / "raw", athlete_id=DEFAULT_ATHLETE_ID)

    assert backfilled == 0
