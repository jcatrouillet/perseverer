"""A planned workout's route: the athlete's own GPX file, summarised for display.

Distinct from `gpx/parser.py`, which turns a *recorded* GPX (timestamps, sensors) into a canonical
activity. A planned route is only geometry -- a name, a distance, an elevation gain and a thinned
polyline to draw on the calendar. The original file is archived byte-for-byte by the caller
(raw first), so none of this is the source of truth and it can always be re-derived.

Untrusted upload: entity declarations are refused outright (billion-laughs / external-entity
tricks) rather than relying on the XML parser's defaults, and the size is capped by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from xml.etree import ElementTree as ET

import polyline as polyline_codec

MAX_GPX_BYTES = 5 * 1024 * 1024
# Enough to draw a smooth route on a card-sized map; a long run is a few thousand points.
MAX_POLYLINE_POINTS = 600
_EARTH_RADIUS_M = 6_371_000.0


class InvalidRouteError(ValueError):
    """The file is not a usable GPX route (malformed, unsafe, or fewer than two points)."""


@dataclass(frozen=True)
class RouteSummary:
    name: str | None
    distance_m: float
    elevation_gain_m: float | None
    encoded_polyline: str
    point_count: int


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = radians(a[0]), radians(a[1]), radians(b[0]), radians(b[1])
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * _EARTH_RADIUS_M * asin(sqrt(h))


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def parse_gpx_route(content: bytes) -> RouteSummary:
    """Name, distance, elevation gain and a thinned encoded polyline from a GPX track (or, failing
    that, route). Raises `InvalidRouteError` for anything that is not a usable route."""
    if len(content) > MAX_GPX_BYTES:
        raise InvalidRouteError("GPX file is too large (5 MB maximum)")
    head = content.lower()
    if b"<!entity" in head or b"<!doctype" in head:
        raise InvalidRouteError("GPX file contains an entity or DOCTYPE declaration")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise InvalidRouteError(f"not valid XML: {exc}") from exc
    if _local(root.tag) != "gpx":
        raise InvalidRouteError("not a GPX file (root element is not <gpx>)")

    track_points: list[ET.Element] = []
    route_points: list[ET.Element] = []
    name: str | None = None
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "trkpt":
            track_points.append(element)
        elif tag == "rtept":
            route_points.append(element)
        elif (tag in ("trk", "rte") and name is None) or (tag == "metadata" and name is None):
            name = _child_text(element, "name")
    points_xml = track_points or route_points

    points: list[tuple[float, float]] = []
    elevations: list[float | None] = []
    for point in points_xml:
        try:
            lat = float(point.attrib["lat"])
            lon = float(point.attrib["lon"])
        except (KeyError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        ele_text = _child_text(point, "ele")
        try:
            elevations.append(float(ele_text) if ele_text is not None else None)
        except ValueError:
            elevations.append(None)
        points.append((lat, lon))
    if len(points) < 2:
        raise InvalidRouteError("the GPX file has fewer than two track points")

    distance = sum(_haversine_m(points[i - 1], points[i]) for i in range(1, len(points)))
    known = [e for e in elevations if e is not None]
    gain: float | None = None
    if len(known) >= 2:
        # Raw GPX elevation is noisy; ignore sub-metre wobble so a flat route doesn't accrue a
        # phantom climb. An approximate figure, shown as such (Garmin's own will differ).
        gain = sum(
            max(0.0, known[i] - known[i - 1])
            for i in range(1, len(known))
            if known[i] - known[i - 1] >= 1.0
        )

    step = max(1, -(-len(points) // MAX_POLYLINE_POINTS))
    thinned = points[::step]
    if thinned[-1] != points[-1]:
        thinned.append(points[-1])
    return RouteSummary(
        name=name,
        distance_m=distance,
        elevation_gain_m=gain,
        encoded_polyline=polyline_codec.encode(thinned),
        point_count=len(points),
    )
