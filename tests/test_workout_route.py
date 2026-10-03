"""parse_gpx_route: a planned workout's GPX route, summarised for display."""

from __future__ import annotations

from collections.abc import Sequence

import polyline as polyline_codec
import pytest

from perseverer.workout_route import MAX_GPX_BYTES, InvalidRouteError, parse_gpx_route

_NS = 'xmlns="http://www.topografix.com/GPX/1/1"'


def _gpx(
    points: Sequence[tuple[float, float, float | None]], name: str | None = "Dam loop"
) -> bytes:
    body = "".join(
        f'<trkpt lat="{lat}" lon="{lon}">'
        + (f"<ele>{ele}</ele>" if ele is not None else "")
        + "</trkpt>"
        for lat, lon, ele in points
    )
    name_xml = f"<name>{name}</name>" if name else ""
    xml = f'<?xml version="1.0"?><gpx {_NS} version="1.1">'
    return f"{xml}<trk>{name_xml}<trkseg>{body}</trkseg></trk></gpx>".encode()


def test_distance_gain_name_and_polyline() -> None:
    # Roughly 1.11 km north, climbing 30 m.
    pts = [(37.0, -122.0, 100.0), (37.005, -122.0, 115.0), (37.01, -122.0, 130.0)]
    summary = parse_gpx_route(_gpx(pts))
    assert summary.name == "Dam loop"
    assert summary.point_count == 3
    assert summary.distance_m == pytest.approx(1112, rel=0.01)
    assert summary.elevation_gain_m == pytest.approx(30.0)
    decoded = polyline_codec.decode(summary.encoded_polyline)
    assert decoded[0] == pytest.approx((37.0, -122.0), abs=1e-4)
    assert decoded[-1] == pytest.approx((37.01, -122.0), abs=1e-4)


def test_flat_noise_does_not_accrue_climb_and_missing_elevation_is_none() -> None:
    wobble = [(37.0, -122.0, 100.0), (37.001, -122.0, 100.4), (37.002, -122.0, 100.0)]
    assert parse_gpx_route(_gpx(wobble)).elevation_gain_m == 0.0
    no_ele = [(37.0, -122.0, None), (37.001, -122.0, None)]
    assert parse_gpx_route(_gpx(no_ele)).elevation_gain_m is None


def test_route_points_are_used_when_there_is_no_track() -> None:
    content = (
        f"<gpx {_NS}><rte><name>Planned</name>"
        '<rtept lat="37.0" lon="-122.0"/><rtept lat="37.01" lon="-122.0"/></rte></gpx>'
    ).encode()
    summary = parse_gpx_route(content)
    assert summary.name == "Planned"
    assert summary.point_count == 2


def test_long_track_is_thinned_but_keeps_the_last_point() -> None:
    pts: list[tuple[float, float, float | None]] = [
        (37.0 + i * 0.0001, -122.0, None) for i in range(5000)
    ]
    decoded = polyline_codec.decode(parse_gpx_route(_gpx(pts)).encoded_polyline)
    assert len(decoded) <= 601
    assert decoded[-1] == pytest.approx(pts[-1][:2], abs=1e-4)


@pytest.mark.parametrize(
    "content",
    [
        b"not xml at all",
        b'<kml xmlns="x"/>',
        _gpx([(37.0, -122.0, None)]),  # a single point is not a route
        b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaa">]><gpx><trk><trkseg/></trk></gpx>',
        (
            b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
            b'<trkpt lat="99" lon="0"/><trkpt lat="1" lon="0"/></trkseg></trk></gpx>'
        ),
    ],
)
def test_unusable_files_are_rejected(content: bytes) -> None:
    with pytest.raises(InvalidRouteError):
        parse_gpx_route(content)


def test_oversized_file_is_rejected() -> None:
    with pytest.raises(InvalidRouteError):
        parse_gpx_route(b"x" * (MAX_GPX_BYTES + 1))
