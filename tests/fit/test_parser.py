"""Golden-file tests for FIT parsing.

Uses a small synthetic fixture built with garmin_fit_sdk's own Encoder (see
tests/fixtures/fit/synthetic_run.fit) rather than any of the real personal FIT files used to
validate this parser during development — those carry real health/location data and were
never committed. Cross-checked against fitdecode, an independent parser, for basic sanity.
"""

from pathlib import Path

import fitdecode

from sporthealth.fit.parser import parse_fit

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
