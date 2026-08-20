"""gpx/parser.py tests -- synthetic GPX content shaped like the real Strava export archive
samples confirmed in Phase 8 (ADR 0012): bare trkpt lat/lon/ele/time, and separately a
gpxtpx:TrackPointExtension carrying heart_rate (the shape seen for GPX files a Garmin device
itself originally produced)."""

from __future__ import annotations

from sporthealth.gpx.parser import parse_gpx

_BARE_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="StravaGPX" version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
 <trk>
  <name>Bryce Canyon hike</name>
  <trkseg>
   <trkpt lat="37.6656890" lon="-112.1102740">
    <ele>2082.6</ele>
    <time>2026-04-17T22:17:09Z</time>
   </trkpt>
   <trkpt lat="37.6657130" lon="-112.1102920">
    <ele>2085.0</ele>
    <time>2026-04-17T22:17:19Z</time>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""

_EXTENDED_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="Garmin Connect" version="1.1" xmlns="http://www.topografix.com/GPX/1/1"
 xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
 <trk>
  <name>Morning Run</name>
  <trkseg>
   <trkpt lat="37.3622550" lon="-121.9745300">
    <ele>17.9</ele>
    <time>2022-09-26T14:24:03Z</time>
    <extensions>
     <gpxtpx:TrackPointExtension>
      <gpxtpx:hr>154</gpxtpx:hr>
      <gpxtpx:cad>88</gpxtpx:cad>
     </gpxtpx:TrackPointExtension>
    </extensions>
   </trkpt>
   <trkpt lat="37.3622600" lon="-121.9745400">
    <ele>18.0</ele>
    <time>2022-09-26T14:24:08Z</time>
    <extensions>
     <gpxtpx:TrackPointExtension>
      <gpxtpx:hr>156</gpxtpx:hr>
      <gpxtpx:mystery>42</gpxtpx:mystery>
     </gpxtpx:TrackPointExtension>
    </extensions>
   </trkpt>
  </trkseg>
 </trk>
</gpx>
"""

_EMPTY_TRACK_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
 <trk><name>No points</name><trkseg></trkseg></trk>
</gpx>
"""


def test_bare_gpx_parses_geometry_only() -> None:
    batch = parse_gpx(_BARE_GPX)
    assert batch.kind == "activity"
    assert batch.activity is not None
    a = batch.activity
    assert a.name == "Bryce Canyon hike"
    assert len(a.stream) == 2
    assert a.stream[0].values["lat"] == 37.6656890
    assert a.stream[0].values["altitude_m"] == 2082.6
    assert "heart_rate" not in a.stream[0].values
    assert a.duration_s == 10.0
    assert a.route_start == (37.6656890, -112.1102740)
    assert a.route_end == (37.6657130, -112.1102920)
    # No summary totals fabricated -- the caller overlays these from activities.csv.
    assert a.distance_m is None
    assert a.sport == "unknown"


def test_distance_and_speed_are_derived_via_haversine() -> None:
    batch = parse_gpx(_BARE_GPX)
    assert batch.activity is not None
    a = batch.activity
    # First point: cumulative distance starts at zero, no speed (nothing to measure it from).
    assert a.stream[0].values["distance_m"] == 0.0
    assert "speed_mps" not in a.stream[0].values
    # Second point, 10s later at a real (if tiny) lat/lon offset: a real, non-fabricated,
    # bounded distance/speed -- not asserting an exact float (a haversine value doesn't need to
    # be hand-verified to the millimetre to prove the derivation is wired up correctly).
    assert 0 < a.stream[1].values["distance_m"] < 10
    assert 0 < a.stream[1].values["speed_mps"] < 5


def test_extended_gpx_captures_hr_cadence_and_catalogs_unknown_extension() -> None:
    batch = parse_gpx(_EXTENDED_GPX)
    assert batch.activity is not None
    a = batch.activity
    assert a.stream[0].values["heart_rate"] == 154
    assert a.stream[0].values["cadence"] == 88
    assert a.stream[1].values["heart_rate"] == 156
    assert "gpx.trackpointextension.mystery" in a.unrecognized_field_keys


def test_empty_track_is_unrecognized_not_a_fabricated_activity() -> None:
    batch = parse_gpx(_EMPTY_TRACK_GPX)
    assert batch.kind == "unrecognized"
    assert batch.activity is None


def test_leading_whitespace_before_xml_declaration_is_tolerated() -> None:
    # A real minority of the sample Strava archive's files (an older export path) have literal
    # whitespace before <?xml ...?>, which Python's expat parser otherwise rejects outright.
    batch = parse_gpx(b"          " + _BARE_GPX)
    assert batch.kind == "activity"
    assert batch.activity is not None
    assert len(batch.activity.stream) == 2


def test_utc_offset_is_derived_from_the_first_trackpoints_coordinates() -> None:
    # GPX carries no local-time field of its own -- every <time> is UTC -- so the offset has to
    # come from where the activity actually happened. _BARE_GPX's coordinates are Bryce Canyon,
    # Utah (Mountain time); in April that's MDT, UTC-6.
    batch = parse_gpx(_BARE_GPX)
    assert batch.activity is not None
    a = batch.activity
    assert a.tz_name == "America/Denver"
    assert a.utc_offset_s == -6 * 3600


def test_utc_offset_derivation_uses_a_real_recorded_point_not_a_fixed_default() -> None:
    # _EXTENDED_GPX's coordinates are the San Jose, CA area (Pacific time); confirms a
    # *different* real GPS point resolves to a genuinely different zone/offset, not some
    # hardcoded stand-in value shared by every fixture.
    batch = parse_gpx(_EXTENDED_GPX)
    assert batch.activity is not None
    a = batch.activity
    assert a.tz_name == "America/Los_Angeles"
    assert a.utc_offset_s == -7 * 3600
