"""Pure GPX parsing -- produces the same CanonicalBatch/CanonicalActivity shape fit/parser.py
produces (see fit/types.py), so ingest_canonical_batch and streams.py need zero changes to
accept GPX output.

Real shape confirmed against a real Strava export archive (Phase 8, ADR 0012): bare
lat/lon/ele/time <trkpt> elements; some files (ones a Garmin device itself originally produced,
re-exported via Strava) additionally carry a <gpxtpx:TrackPointExtension> with a heart_rate
child -- the v1 schema also defines cadence/temperature children, but only heart_rate was
observed in the real sample archive; cadence/temperature are still materialized below since
they're part of the same schema, and any other extension child is cataloged as unrecognized,
never dropped. GPX carries no laps, no session-level summary, no device identity, and no
distance/elevation-gain *totals* -- these stay unset here rather than fabricated; the caller
(adapters/strava_export.py) overlays the vendor's own totals from activities.csv, which is
authoritative and already available at parse time (unlike Garmin's post-hoc
summarizedActivities correction pass). GPX also carries no distance/speed field at all (unlike
TCX's DistanceMeters), so a *per-point* `distance_m`/`speed_mps` stream -- needed for the Pace/
GAP chart panels and the per-km splits table, neither of which GPX-sourced activities had before
this -- is derived here via the haversine great-circle distance between consecutive points
(ADR 0013); this is a real per-point stream value, not a fabricated summary total.

Matches child elements by local tag name (ignoring namespace prefix/URI) rather than exact
qualified names -- GPX 1.1's core elements are always in the default GPX namespace, but the
sample archive already showed a track-point extension from a Garmin-specific namespace, and a
different exporting tool could plausibly use a different extension namespace or prefix. Local-name
matching is the same pragmatic choice made in tcx/parser.py for the same reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt
from xml.etree import ElementTree as ET

from sporthealth.fit.types import CanonicalActivity, CanonicalBatch, ParsedMetric, StreamPoint
from sporthealth.timezone_lookup import offset_from_coordinates

_LatLon = tuple[float, float]
_BBox = tuple[float, float, float, float]

_EARTH_RADIUS_M = 6_371_000.0


def _haversine_m(a: _LatLon, b: _LatLon) -> float:
    """Great-circle distance between two lat/lon points, in metres (mean Earth radius) -- GPX
    carries no distance field of its own, unlike TCX's DistanceMeters, so this is the only way
    to get a per-point distance/speed stream for the Pace/GAP chart panels and the per-km splits
    table, both of which need one (see ADR 0013)."""
    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * asin(sqrt(h))


def _add_distance_and_speed(stream: list[StreamPoint], route_points: list[_LatLon]) -> None:
    """Mutates each StreamPoint's `values` dict in place (the frozen dataclass only blocks
    reassigning the `values` attribute itself, not mutating the dict it already points to) --
    adds cumulative `distance_m` to every point, and `speed_mps` to every point after the first
    (a leg's speed needs a previous point to measure from)."""
    cumulative = 0.0
    for i, point in enumerate(stream):
        if i > 0:
            leg_m = _haversine_m(route_points[i - 1], route_points[i])
            cumulative += leg_m
            dt = (point.timestamp_utc - stream[i - 1].timestamp_utc).total_seconds()
            if dt > 0:
                point.values["speed_mps"] = leg_m / dt
        point.values["distance_m"] = cumulative


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_local(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if _local(child.tag) == name:
            return child
    return None


def _findall_local(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent if _local(child.tag) == name]


def _parse_time(text: str) -> datetime:
    # GPX times are always UTC ("Z" suffix) per the 1.1 schema -- confirmed against the real
    # sample archive, every <time> element observed ended in "Z".
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


def _route_endpoints(points: list[_LatLon]) -> tuple[_LatLon | None, _LatLon | None, _BBox | None]:
    if not points:
        return None, None, None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return points[0], points[-1], (min(lats), min(lons), max(lats), max(lons))


def parse_gpx(content: bytes) -> CanonicalBatch:
    # A real minority of the sample archive's files (an older export path -- the one confirmed
    # case was a 2020 activity) have literal leading whitespace before the <?xml ...?>
    # declaration, which Python's expat parser rejects outright ("XML or text declaration not
    # at start of entity"). Confirmed by decoding a real failing file, not assumed.
    root = ET.fromstring(content.lstrip())
    trk = _find_local(root, "trk")
    if trk is None:
        return CanonicalBatch(
            kind="unrecognized", activity=None, unrecognized_message_types=["gpx_no_track"]
        )

    name_el = _find_local(trk, "name")
    name = name_el.text if name_el is not None and name_el.text else None

    extra_metrics: list[ParsedMetric] = []
    creator = root.get("creator")
    if creator:
        extra_metrics.append(ParsedMetric(key="gpx.creator", value_num=None, value_text=creator))

    stream: list[StreamPoint] = []
    route_points: list[_LatLon] = []
    unrecognized: set[str] = set()

    for trkseg in _findall_local(trk, "trkseg"):
        for trkpt in _findall_local(trkseg, "trkpt"):
            lat = trkpt.get("lat")
            lon = trkpt.get("lon")
            time_el = _find_local(trkpt, "time")
            if lat is None or lon is None or time_el is None or not time_el.text:
                continue
            lat_f, lon_f = float(lat), float(lon)
            route_points.append((lat_f, lon_f))
            ts = _parse_time(time_el.text)

            values: dict[str, float] = {"lat": lat_f, "lon": lon_f}
            ele_el = _find_local(trkpt, "ele")
            if ele_el is not None and ele_el.text:
                values["altitude_m"] = float(ele_el.text)

            ext = _find_local(trkpt, "extensions")
            if ext is not None:
                tpx = _find_local(ext, "TrackPointExtension")
                if tpx is not None:
                    for child in tpx:
                        local = _local(child.tag)
                        if not child.text:
                            continue
                        if local == "hr":
                            values["heart_rate"] = float(child.text)
                        elif local == "cad":
                            values["cadence"] = float(child.text)
                        elif local == "atemp":
                            values["temperature"] = float(child.text)
                        else:
                            unrecognized.add(f"gpx.trackpointextension.{local}")

            stream.append(StreamPoint(timestamp_utc=ts, values=values))

    if not stream:
        return CanonicalBatch(
            kind="unrecognized", activity=None, unrecognized_message_types=["gpx_empty_track"]
        )
    _add_distance_and_speed(stream, route_points)

    start_time = stream[0].timestamp_utc
    duration_s = (stream[-1].timestamp_utc - start_time).total_seconds()
    route_start, route_end, route_bbox = _route_endpoints(route_points)
    # GPX carries no local-time field of its own -- every <trkpt> timestamp is UTC -- so the
    # only way to get the true calendar date/local time right is to derive it from where the
    # activity actually happened, not assume UTC=local (which silently shifts an activity onto
    # the wrong calendar day for any non-UTC athlete). See timezone_lookup.py.
    utc_offset_s, tz_name = (
        offset_from_coordinates(route_points[0][0], route_points[0][1], start_time)
        if route_points
        else (0, None)
    )

    activity = CanonicalActivity(
        start_time_utc=start_time,
        utc_offset_s=utc_offset_s,
        tz_name=tz_name,
        sport="unknown",
        sub_sport=None,
        name=name,
        duration_s=duration_s,
        moving_duration_s=None,
        distance_m=None,
        elevation_gain_m=None,
        calories=None,
        device=None,
        stream=stream,
        route_points=route_points,
        route_start=route_start,
        route_end=route_end,
        route_bbox=route_bbox,
        extra_metrics=extra_metrics,
        unrecognized_field_keys=sorted(unrecognized),
    )
    return CanonicalBatch(kind="activity", activity=activity)
