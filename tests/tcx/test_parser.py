"""tcx/parser.py tests -- synthetic TCX content shaped like the real Strava export archive
sample confirmed in Phase 8 (ADR 0012): a Lap with real summary fields (avg/max HR, distance,
duration) plus Trackpoints carrying Time/Position/DistanceMeters/HeartRateBpm, and a second
device's TPX-extension cadence to confirm the local-name-based extension lookup."""

from __future__ import annotations

from sporthealth.tcx.parser import parse_tcx

_TCX_WITH_LAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities>
  <Activity Sport="Running">
   <Id>2021-06-11T14:01:53.131Z</Id>
   <Lap StartTime="2021-06-11T14:01:53.131Z">
    <TotalTimeSeconds>15.0</TotalTimeSeconds>
    <DistanceMeters>50.0</DistanceMeters>
    <Calories>10</Calories>
    <AverageHeartRateBpm><Value>142</Value></AverageHeartRateBpm>
    <MaximumHeartRateBpm><Value>150</Value></MaximumHeartRateBpm>
    <Track>
     <Trackpoint>
      <Time>2021-06-11T14:01:54.131Z</Time>
      <Position><LatitudeDegrees>37.3621217</LatitudeDegrees><LongitudeDegrees>-121.9744830</LongitudeDegrees></Position>
      <DistanceMeters>0.0</DistanceMeters>
      <HeartRateBpm><Value>140</Value></HeartRateBpm>
     </Trackpoint>
     <Trackpoint>
      <Time>2021-06-11T14:02:09.131Z</Time>
      <Position><LatitudeDegrees>37.3622100</LatitudeDegrees><LongitudeDegrees>-121.9744000</LongitudeDegrees></Position>
      <DistanceMeters>50.0</DistanceMeters>
      <HeartRateBpm><Value>145</Value></HeartRateBpm>
      <Extensions>
       <ns3:TPX xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">
        <ns3:RunCadence>88</ns3:RunCadence>
       </ns3:TPX>
      </Extensions>
     </Trackpoint>
    </Track>
   </Lap>
   <Creator xsi:type="Device_t" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <Name>Polar BEAT</Name>
   </Creator>
  </Activity>
 </Activities>
</TrainingCenterDatabase>
"""

_TCX_NO_ACTIVITY = b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities></Activities>
</TrainingCenterDatabase>
"""


def test_tcx_lap_and_trackpoints_are_parsed() -> None:
    batch = parse_tcx(_TCX_WITH_LAP)
    assert batch.kind == "activity"
    assert batch.activity is not None
    a = batch.activity
    assert a.sport == "running"
    assert len(a.laps) == 1
    lap = a.laps[0]
    assert lap.distance_m == 50.0
    assert lap.duration_s == 15.0
    assert lap.avg_hr == 142
    assert lap.max_hr == 150
    assert lap.avg_speed_mps == 50.0 / 15.0

    assert len(a.stream) == 2
    assert a.stream[0].values["heart_rate"] == 140
    assert a.stream[0].values["lat"] == 37.3621217
    assert "altitude_m" not in a.stream[0].values  # device had no barometric altimeter
    assert a.stream[1].values["cadence"] == 88  # from the vendor-namespaced TPX extension
    # Speed derived from the file's own cumulative DistanceMeters, not fabricated: (50-0)/15s.
    assert "speed_mps" not in a.stream[0].values  # nothing to measure the first point against
    assert a.stream[1].values["speed_mps"] == 50.0 / 15.0

    assert a.device is not None
    assert a.device.product == "Polar BEAT"
    assert a.duration_s == 15.0
    # No CSV overlay applied here -- the caller (adapters/strava_export.py) does that.
    assert a.distance_m is None


def test_tcx_with_no_activity_is_unrecognized() -> None:
    batch = parse_tcx(_TCX_NO_ACTIVITY)
    assert batch.kind == "unrecognized"
    assert batch.activity is None


def test_leading_whitespace_before_xml_declaration_is_tolerated() -> None:
    # A real activity in the sample Strava archive (a 2020 upload, an older export path) had
    # ten literal spaces before <?xml ...?>, which Python's expat parser otherwise rejects
    # outright ("XML or text declaration not at start of entity").
    batch = parse_tcx(b"          " + _TCX_WITH_LAP)
    assert batch.kind == "activity"
    assert batch.activity is not None
    assert len(batch.activity.stream) == 2


def test_utc_offset_is_derived_from_the_first_trackpoints_coordinates() -> None:
    # TCX carries no local-time field of its own either -- every <Time> is UTC -- so the offset
    # has to come from where the activity actually happened. This fixture's coordinates are the
    # San Jose, CA area (Pacific time); in June that's PDT, UTC-7.
    batch = parse_tcx(_TCX_WITH_LAP)
    assert batch.activity is not None
    a = batch.activity
    assert a.tz_name == "America/Los_Angeles"
    assert a.utc_offset_s == -7 * 3600
