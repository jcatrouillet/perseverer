"""Golden-file tests for FIT parsing.

Uses a small synthetic fixture built with garmin_fit_sdk's own Encoder (see
tests/fixtures/fit/synthetic_run.fit) rather than any of the real personal FIT files used to
validate this parser during development — those carry real health/location data and were
never committed. Cross-checked against fitdecode, an independent parser, for basic sanity.
"""

from pathlib import Path

import fitdecode

from sporthealth.fit.parser import _time_in_zone_metrics, parse_fit

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_run.fit"


def test_parses_synthetic_activity() -> None:
    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.kind == "activity"
    a = batch.activity
    assert a is not None
    assert a.sport == "running"
    assert a.sub_sport == "generic"
    assert a.duration_s == 600.0
    assert a.distance_m == 1500.0
    assert a.calories == 80.0
    assert a.elevation_gain_m == 5.0
    assert a.device is not None
    assert a.device.serial_number == "999888777"
    assert a.utc_offset_s == -28800  # baked into local_timestamp in the fixture


def test_stream_channels_extracted() -> None:
    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.activity is not None
    assert len(batch.activity.stream) == 2
    first = batch.activity.stream[0]
    assert first.values["heart_rate"] == 100.0
    assert first.values["altitude_m"] == 10.0


def test_laps_extracted() -> None:
    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.activity is not None
    assert len(batch.activity.laps) == 1
    assert batch.activity.laps[0].distance_m == 750.0


def test_unhandled_but_valid_record_field_is_cataloged_not_dropped() -> None:
    """`grade` is a real FIT record field our parser doesn't map to a channel — it must still
    surface somewhere (never silently discarded), just not as a materialized stream value."""
    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.activity is not None
    assert "fit.record.grade" in batch.activity.unrecognized_field_keys
    assert "grade" not in batch.activity.stream[0].values


def test_unmapped_session_field_becomes_extra_metric() -> None:
    """`total_grit` has no dedicated CanonicalActivity column — it must flow into
    extra_metrics rather than being dropped (this is what backs metric_definition)."""
    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.activity is not None
    values = {m.key: m.value_num for m in batch.activity.extra_metrics}
    assert values["fit.session.total_grit"] == 12.5


def test_time_in_zone_metrics_expands_real_device_shape() -> None:
    """Shape is a direct transcription of a real decoded `time_in_zone_mesgs` row (7 HR zone
    buckets for 6 configured boundaries -- index 0 is "time below zone 1"), not invented -- see
    `_time_in_zone_metrics`'s own docstring for where this was introspected from."""
    row = {
        "timestamp": "2023-09-30T22:13:07Z",
        "time_in_hr_zone": [729.172, 1544.902, 77.001, 0.0, 0.0, 0.0, 0.0],
        "reference_mesg": "session",
        "reference_index": 0,
        "hr_zone_high_boundary": [87, 106, 126, 140, 161, 177],
        "hr_calc_type": "percent_max_hr",
        "max_heart_rate": 177,
        "resting_heart_rate": 47,
        "threshold_heart_rate": 0,
    }
    metrics = {m.key: (m.value_num, m.value_text) for m in _time_in_zone_metrics(row)}

    assert metrics["fit.time_in_zone.time_in_hr_zone_0"] == (729.172, None)
    assert metrics["fit.time_in_zone.time_in_hr_zone_1"] == (1544.902, None)
    assert metrics["fit.time_in_zone.time_in_hr_zone_6"] == (0.0, None)
    assert metrics["fit.time_in_zone.hr_zone_high_boundary_0"] == (87.0, None)
    assert metrics["fit.time_in_zone.hr_zone_high_boundary_5"] == (177.0, None)
    # Scalar (non-list) fields on the same row are captured too, text ones included.
    assert metrics["fit.time_in_zone.max_heart_rate"] == (177.0, None)
    assert metrics["fit.time_in_zone.hr_calc_type"] == (None, "percent_max_hr")


def test_time_in_zone_metrics_handles_a_none_inside_a_zone_boundary_array() -> None:
    """A real cycling file's `power_zone_high_boundary` carried a trailing `None` (an
    open-ended top zone) -- must skip that one index rather than crash or fabricate a value."""
    row = {"power_zone_high_boundary": [313, 386, 434, 482, 554, 4000, 0, None]}
    metrics = {m.key: m.value_num for m in _time_in_zone_metrics(row)}
    assert metrics["fit.time_in_zone.power_zone_high_boundary_5"] == 4000.0
    assert "fit.time_in_zone.power_zone_high_boundary_7" not in metrics


def test_time_in_zone_absent_from_a_file_with_no_such_message() -> None:
    """The synthetic fixture has no time_in_zone_mesgs at all -- some real devices/firmware
    never emit it. Must parse cleanly with zero time-in-zone metrics, not error."""
    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.activity is not None
    keys = [m.key for m in batch.activity.extra_metrics]
    assert not any(k.startswith("fit.time_in_zone.") for k in keys)


def test_cross_check_against_fitdecode() -> None:
    """Independent parser cross-check: fitdecode must agree on basic session facts."""
    with fitdecode.FitReader(str(FIXTURE)) as fit:
        session_frames = [
            frame
            for frame in fit
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "session"
        ]
    assert len(session_frames) == 1
    session = session_frames[0]

    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.activity is not None
    assert session.get_value("total_distance") == batch.activity.distance_m
    assert session.get_value("total_calories") == batch.activity.calories
    assert session.get_value("sport") == batch.activity.sport
