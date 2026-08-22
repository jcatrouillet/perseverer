"""Pure TCX parsing -- produces the same CanonicalBatch/CanonicalActivity shape fit/parser.py
produces (see fit/types.py), so ingest_canonical_batch and streams.py need zero changes to
accept TCX output.

Real shape confirmed against a real Strava export archive (Phase 8, ADR 0012): one <Activity>
with one or more <Lap> elements (materialized as real ParsedLap rows -- TCX genuinely has lap
structure, unlike GPX), each containing <Track><Trackpoint> elements with Time/Position/
DistanceMeters/HeartRateBpm, and -- device-dependent -- AltitudeMeters (absent for at least one
real sample archive device, a Polar BEAT with no barometric altimeter; never fabricated when
missing) and a <Cadence>/<Extensions><TPX> block for devices that record it. Sport/name come
from the vendor's own <Activity Sport="..."> attribute; the caller
(adapters/strava_export.py) overlays activities.csv's own totals (distance/duration/elevation/
calories) the same way it does for GPX, since the CSV is the higher-fidelity, already-available
source for those summary fields.

Matches child elements by local tag name (ignoring namespace prefix/URI), not exact qualified
names: the sample archive's TCX files declare the standard TrainingCenterDatabase/v2 namespace,
but the vendor-extension block (cadence/watts) is device-specific and a different real device
could plausibly use a different extension namespace or an unprefixed one -- matching by local
name is robust to that variation without needing to enumerate every vendor's schema from memory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from perseverer.fit.types import (
    CanonicalActivity,
    CanonicalBatch,
    ParsedDevice,
    ParsedLap,
    StreamPoint,
)
from perseverer.timezone_lookup import offset_from_coordinates

_LatLon = tuple[float, float]
_BBox = tuple[float, float, float, float]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_local(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if _local(child.tag) == name:
            return child
    return None


def _find_local_recursive(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent.iter():
        if _local(child.tag) == name:
            return child
    return None


def _findall_local(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent if _local(child.tag) == name]


def _text_float(parent: ET.Element, name: str) -> float | None:
    el = _find_local(parent, name)
    if el is None or not el.text:
        return None
    try:
        return float(el.text)
    except ValueError:
        return None


def _value_float(parent: ET.Element, name: str) -> float | None:
    """For the TCX <Foo><Value>N</Value></Foo> wrapper pattern (HeartRateBpm, etc)."""
    el = _find_local(parent, name)
    if el is None:
        return None
    return _text_float(el, "Value")


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


def _route_endpoints(points: list[_LatLon]) -> tuple[_LatLon | None, _LatLon | None, _BBox | None]:
    if not points:
        return None, None, None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return points[0], points[-1], (min(lats), min(lons), max(lats), max(lons))


def _add_speed(stream: list[StreamPoint]) -> None:
    """TCX's own DistanceMeters is already cumulative across the whole activity (confirmed
    against a real sample: monotonically increasing across lap boundaries, not reset per lap),
    so speed is just the distance delta between consecutive points -- unlike GPX, which has no
    distance field at all and needs the haversine derivation in gpx/parser.py instead. Mutates
    each StreamPoint's `values` dict in place, same as that module's `_add_distance_and_speed`.
    Only computed where both this point and the previous one actually have `distance_m` --
    AltitudeMeters-style per-device gaps mean a real file can have distance on some points and
    not others."""
    for i in range(1, len(stream)):
        d0 = stream[i - 1].values.get("distance_m")
        d1 = stream[i].values.get("distance_m")
        if d0 is None or d1 is None:
            continue
        dt = (stream[i].timestamp_utc - stream[i - 1].timestamp_utc).total_seconds()
        if dt > 0:
            stream[i].values["speed_mps"] = (d1 - d0) / dt


def _parse_trackpoint(trkpt: ET.Element) -> tuple[StreamPoint, _LatLon | None] | None:
    time_el = _find_local(trkpt, "Time")
    if time_el is None or not time_el.text:
        return None
    ts = _parse_time(time_el.text)

    values: dict[str, float] = {}
    point: _LatLon | None = None
    pos = _find_local(trkpt, "Position")
    if pos is not None:
        lat = _text_float(pos, "LatitudeDegrees")
        lon = _text_float(pos, "LongitudeDegrees")
        if lat is not None and lon is not None:
            point = (lat, lon)
            values["lat"] = lat
            values["lon"] = lon

    altitude = _text_float(trkpt, "AltitudeMeters")
    if altitude is not None:
        values["altitude_m"] = altitude

    distance = _text_float(trkpt, "DistanceMeters")
    if distance is not None:
        values["distance_m"] = distance

    hr = _value_float(trkpt, "HeartRateBpm")
    if hr is not None:
        values["heart_rate"] = hr

    cadence = _text_float(trkpt, "Cadence")
    if cadence is None:
        tpx = _find_local_recursive(trkpt, "TPX")
        if tpx is not None:
            cadence = _text_float(tpx, "RunCadence") or _text_float(tpx, "Cadence")
            watts = _text_float(tpx, "Watts")
            if watts is not None:
                values["power"] = watts
    if cadence is not None:
        values["cadence"] = cadence

    return StreamPoint(timestamp_utc=ts, values=values), point


def parse_tcx(content: bytes) -> CanonicalBatch:
    # A real minority of the sample archive's files (an older export path -- the confirmed
    # failing case was a 2020 activity) have literal leading whitespace before the <?xml ...?>
    # declaration, which Python's expat parser rejects outright ("XML or text declaration not
    # at start of entity"). Confirmed by decoding a real failing file, not assumed.
    root = ET.fromstring(content.lstrip())
    activities_el = _find_local(root, "Activities")
    activity_el = _find_local(activities_el, "Activity") if activities_el is not None else None
    if activity_el is None:
        return CanonicalBatch(
            kind="unrecognized", activity=None, unrecognized_message_types=["tcx_no_activity"]
        )

    sport_attr = activity_el.get("Sport")

    laps: list[ParsedLap] = []
    stream: list[StreamPoint] = []
    route_points: list[_LatLon] = []

    for lap_index, lap_el in enumerate(_findall_local(activity_el, "Lap")):
        lap_start_text = lap_el.get("StartTime")
        lap_start = _parse_time(lap_start_text) if lap_start_text else None

        lap_distance = _text_float(lap_el, "DistanceMeters")
        lap_duration = _text_float(lap_el, "TotalTimeSeconds")
        avg_hr = _value_float(lap_el, "AverageHeartRateBpm")
        max_hr = _value_float(lap_el, "MaximumHeartRateBpm")
        avg_speed_mps = None
        if lap_distance is not None and lap_duration:
            avg_speed_mps = lap_distance / lap_duration

        if lap_start is not None:
            laps.append(
                ParsedLap(
                    lap_index=lap_index,
                    start_time_utc=lap_start,
                    duration_s=lap_duration,
                    # TCX's schema has no separate pause-excluded timer field the way FIT does
                    # (see fit/parser.py's ParsedLap construction) -- TotalTimeSeconds is all
                    # there is, so this just mirrors duration_s rather than fabricating a value.
                    moving_duration_s=lap_duration,
                    distance_m=lap_distance,
                    avg_hr=avg_hr,
                    max_hr=max_hr,
                    avg_speed_mps=avg_speed_mps,
                )
            )

        track_el = _find_local(lap_el, "Track")
        if track_el is None:
            continue
        for trkpt in _findall_local(track_el, "Trackpoint"):
            parsed = _parse_trackpoint(trkpt)
            if parsed is None:
                continue
            point, latlon = parsed
            stream.append(point)
            if latlon is not None:
                route_points.append(latlon)

    if not stream:
        return CanonicalBatch(
            kind="unrecognized", activity=None, unrecognized_message_types=["tcx_empty_track"]
        )
    _add_speed(stream)

    device = None
    creator = _find_local(activity_el, "Creator")
    if creator is not None:
        name_el = _find_local(creator, "Name")
        if name_el is not None and name_el.text:
            device = ParsedDevice(manufacturer=None, product=name_el.text, serial_number=None)

    start_time = stream[0].timestamp_utc
    duration_s = (stream[-1].timestamp_utc - start_time).total_seconds()
    route_start, route_end, route_bbox = _route_endpoints(route_points)
    # TCX carries no local-time field of its own either -- every <Time> is UTC -- same fix as
    # gpx/parser.py, see timezone_lookup.py.
    utc_offset_s, tz_name = (
        offset_from_coordinates(route_points[0][0], route_points[0][1], start_time)
        if route_points
        else (0, None)
    )

    activity = CanonicalActivity(
        start_time_utc=start_time,
        utc_offset_s=utc_offset_s,
        tz_name=tz_name,
        sport=(sport_attr or "unknown").lower(),
        sub_sport=None,
        name=None,
        duration_s=duration_s,
        moving_duration_s=None,
        distance_m=None,
        elevation_gain_m=None,
        calories=None,
        device=device,
        laps=laps,
        stream=stream,
        route_points=route_points,
        route_start=route_start,
        route_end=route_end,
        route_bbox=route_bbox,
    )
    return CanonicalBatch(kind="activity", activity=activity)
