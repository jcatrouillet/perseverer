"""Nominatim (OpenStreetMap) reverse geocoding, per activity -- fetched lazily on first request
for a GPS-bearing activity, archived raw (raw-first, same as every other vendor call in this
project), then cached forever in `activity_metric` so no activity's location is ever looked up
twice. Mirrors weather.py's exact design (read-through cache, archive-then-parse, `None` rather
than a fabricated result).

Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/) caps
unauthenticated use at 1 request/second and requires an identifying User-Agent -- both honored
here: a rate limiter shared across the process, and a real User-Agent naming this project.

Real response shape confirmed via live calls (not assumed): `address` breaks the point down
into whichever administrative levels OSM has data for (city/town/village/hamlet, county, state,
country). A live check against real national-park coordinates (Yosemite, Yellowstone) found
that Nominatim's default `/reverse` layer priority favors small, specific features (a road, a
creek, a geyser) over large park/reserve polygons even when the point sits inside one -- so
"national park" naming only wins when the *closest* feature itself is tagged as a park/reserve
(`addresstype` in `_PARK_ADDRESS_TYPES`, e.g. a `/search` for "Yosemite National Park" itself
returns `addresstype: "nature_reserve"`) or the address breakdown explicitly names one. This is
a real, verified best-effort, not a guarantee for every point recorded inside a park boundary.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import Connection, delete, select

from perseverer.archive import archive_raw_bytes
from perseverer.db.schema import activity_metric
from perseverer.metrics.registry import get_or_register_metric

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
SOURCE = "nominatim"
USER_AGENT = "perseverer/0.1 (self-hosted personal fitness archive; github.com issues)"

METRIC_LOCATION_NAME = "geocoding.nominatim.location_name"

_PARK_ADDRESS_TYPES = frozenset({"national_park", "nature_reserve", "protected_area"})

# Nominatim's own policy: max 1 request/second, unauthenticated use. A process-wide lock (not
# per-connection) because activity detail pages can be requested concurrently by the same
# athlete, and this must throttle across all of them, not just serialize within one request.
_MIN_REQUEST_INTERVAL_S = 1.05
_throttle_lock = threading.Lock()
_last_request_monotonic: float | None = None


def _throttle() -> None:
    global _last_request_monotonic
    with _throttle_lock:
        if _last_request_monotonic is not None:
            elapsed = time.monotonic() - _last_request_monotonic
            if elapsed < _MIN_REQUEST_INTERVAL_S:
                time.sleep(_MIN_REQUEST_INTERVAL_S - elapsed)
        _last_request_monotonic = time.monotonic()


def parse_nominatim_response(raw: dict[str, Any]) -> str | None:
    """Pure parse: prefers a park/reserve name when the point's own closest feature is tagged as
    one, otherwise the most specific place name in the address breakdown (city/town/village/
    hamlet, falling back to county), with a state or country suffix for context. Returns None
    if the response carries no usable place name at all."""
    if raw.get("addresstype") in _PARK_ADDRESS_TYPES and raw.get("name"):
        return str(raw["name"])

    address = raw.get("address")
    if not isinstance(address, dict):
        return None

    for key in ("national_park", "protected_area", "nature_reserve"):
        if address.get(key):
            return str(address[key])

    place = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("municipality")
        or address.get("county")
    )
    if not place:
        return None

    state = address.get("state")
    country = address.get("country")
    if state:
        return f"{place}, {state}"
    if country:
        return f"{place}, {country}"
    return str(place)


def read_cached_location(conn: Connection, athlete_id: str, activity_id: str) -> str | None:
    """Cache-only read, no network call ever -- used both by get_or_fetch_activity_location's
    own read-through-cache check below, and directly by the API router to decide whether a
    request can answer immediately or needs to hand the actual Nominatim fetch off to a
    background task (see routers/activities.py::get_activity_location)."""
    row = conn.execute(
        select(activity_metric.c.value_text).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.source == SOURCE,
            activity_metric.c.metric_key == METRIC_LOCATION_NAME,
        )
    ).fetchone()
    return row.value_text if row is not None else None


def _store(conn: Connection, athlete_id: str, activity_id: str, location_name: str) -> None:
    # Delete-then-insert rather than a blind insert: safe to re-run if an earlier attempt for
    # this activity already wrote a row (matches weather.py's own _store convention).
    conn.execute(
        delete(activity_metric).where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.activity_id == activity_id,
            activity_metric.c.source == SOURCE,
            activity_metric.c.metric_key == METRIC_LOCATION_NAME,
        )
    )
    get_or_register_metric(
        conn,
        metric_key=METRIC_LOCATION_NAME,
        source=SOURCE,
        display_name="Location name (reverse-geocoded)",
        unit_si=None,
        category="activity",
        value_type="text",
    )
    conn.execute(
        activity_metric.insert().values(
            athlete_id=athlete_id,
            activity_id=activity_id,
            metric_key=METRIC_LOCATION_NAME,
            value_num=None,
            value_text=location_name,
            unit=None,
            source=SOURCE,
            created_at=datetime.now(UTC),
        )
    )


def get_or_fetch_activity_location(
    conn: Connection,
    archive_root: Path,
    *,
    athlete_id: str,
    activity_id: str,
    lat: float,
    lon: float,
    client: httpx.Client | None = None,
) -> str | None:
    """Read-through cache: returns the already-stored location name if present (no network call
    -- an activity's location never changes, so it's looked up at most once ever), otherwise
    reverse-geocodes via Nominatim, archives the raw response, parses + stores the name, and
    returns it.

    Returns None (never a fabricated name) if there's nothing cached and the fetch fails, or the
    response has no usable place name. Does not commit -- the caller controls the transaction,
    matching archive_raw_bytes' own convention.
    """
    cached = read_cached_location(conn, athlete_id, activity_id)
    if cached is not None:
        return cached

    params = {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2", "addressdetails": "1"}
    owns_client = client is None
    http_client = client or httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT})
    try:
        _throttle()
        response = http_client.get(NOMINATIM_REVERSE_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            http_client.close()

    archive_raw_bytes(
        conn,
        archive_root,
        athlete_id=athlete_id,
        source=SOURCE,
        kind="nominatim_reverse_json",
        content=response.content,
        locator=f"reverse/{lat:.6f},{lon:.6f}",
        external_id=activity_id,
        http_status=response.status_code,
    )

    try:
        raw = json.loads(response.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None

    location_name = parse_nominatim_response(raw)
    if location_name is None:
        return None

    _store(conn, athlete_id, activity_id, location_name)
    return location_name
