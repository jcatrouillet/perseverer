"""Golden-file tests for FIT parsing.

Uses a small synthetic fixture built with garmin_fit_sdk's own Encoder (see
tests/fixtures/fit/synthetic_run.fit) rather than any of the real personal FIT files used to
validate this parser during development — those carry real health/location data and were
never committed. Cross-checked against fitdecode, an independent parser, for basic sanity.
"""

from datetime import UTC, datetime
from pathlib import Path

import fitdecode

from perseverer.fit.parser import (
    FIT_EPOCH,
    _climb_fields,
    _derive_utc_offset_s,
    _parse_workout,
    _time_in_zone_metrics,
    parse_fit,
)

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


def test_derive_utc_offset_s_plausible_value_is_kept() -> None:
    ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    utc_seconds = (ts - FIT_EPOCH).total_seconds()
    # -8h local (PST): local_timestamp is FIT-epoch-relative seconds at the local wall clock.
    local_ts = utc_seconds - 8 * 3600
    offset = _derive_utc_offset_s({"timestamp": ts, "local_timestamp": local_ts})
    assert offset == -8 * 3600


def test_derive_utc_offset_s_implausible_value_falls_back_to_zero() -> None:
    # A real device quirk seen in 14 real indoor-cycling activities (no GPS fix): local_timestamp
    # comes back corrupt, producing an offset nowhere near a real timezone (~1.1 billion seconds
    # off) -- must not silently corrupt local_date by decades.
    ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    offset = _derive_utc_offset_s({"timestamp": ts, "local_timestamp": -1_102_564_764})
    assert offset == 0


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


def test_lap_moving_duration_uses_total_timer_time() -> None:
    """`duration_s` is FIT's total_elapsed_time (real wall-clock); `moving_duration_s` is
    total_timer_time, which excludes a device pause within the lap -- equal here since the
    fixture's one lap has no pause, but a real paused lap has elapsed >> timer (confirmed
    against a real archived FIT file: 1008.2s vs 90.0s across a genuine ~15min pause)."""
    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.activity is not None
    lap = batch.activity.laps[0]
    assert lap.moving_duration_s == 300.0
    assert lap.moving_duration_s == lap.duration_s


class TestClimbFields:
    """Bouldering's per-route data -- undocumented FIT split fields, reverse-engineered by
    decoding two real activities against their own logged route sequences (19 routes in one, 8
    in the other) and pairing each "climb_active" split, in order, against the athlete's own
    recorded grade+status. grade (70) and result (71) matched on all 27 real routes; avg/max HR
    (15/16) were separately confirmed (avg <= max held on all 55 real splits across both files,
    values fell in a plausible bpm range). These tests fix the exact mapping found there so it
    can't silently drift."""

    def test_maps_a_completed_route(self) -> None:
        # Raw grade 3 -> V2 (offset by -1); raw result 3 -> "completed".
        grade, result, avg_hr, max_hr = _climb_fields(
            {"split_type": "climb_active", 70: 3, 71: 3, 15: 100, 16: 120}
        )
        assert grade == 2
        assert result == "completed"
        assert avg_hr == 100.0
        assert max_hr == 120.0

    def test_maps_an_attempted_route(self) -> None:
        # Raw grade 5 -> V4; raw result 2 -> "attempt".
        grade, result, _avg_hr, _max_hr = _climb_fields(
            {"split_type": "climb_active", 70: 5, 71: 2}
        )
        assert grade == 4
        assert result == "attempt"

    def test_an_unrecognized_result_value_is_kept_not_dropped(self) -> None:
        """Only 2 and 3 have ever been confirmed against real data -- a third value is real
        signal the mapping is incomplete, so it must surface honestly, not silently vanish or
        get guessed at as one of the two known outcomes."""
        grade, result, _avg_hr, _max_hr = _climb_fields(
            {"split_type": "climb_active", 70: 3, 71: 9}
        )
        assert grade == 2
        assert result == "unknown_9"

    def test_a_rest_split_never_carries_grade_or_result(self) -> None:
        """Confirmed against real data: "climb_rest" splits never carry fields 70/71 at all --
        but even if one somehow did, it must not be read as route data, since a rest interval
        isn't a route."""
        grade, result, _avg_hr, _max_hr = _climb_fields(
            {"split_type": "climb_rest", 70: 3, 71: 3}
        )
        assert grade is None
        assert result is None

    def test_a_rest_split_still_carries_heart_rate(self) -> None:
        """Unlike grade/result, avg/max HR are real on a "climb_rest" split too (confirmed
        against real data) -- the rest interval between routes still has a heart rate."""
        _grade, _result, avg_hr, max_hr = _climb_fields(
            {"split_type": "climb_rest", 15: 90, 16: 95}
        )
        assert avg_hr == 90.0
        assert max_hr == 95.0

    def test_a_non_bouldering_split_has_no_climb_fields_at_all(self) -> None:
        """Field numbers 15/16/70/71 aren't confirmed to mean the same thing outside a
        climbing split_type -- a running activity's own interval split must not have its
        unrelated fields misread as climb grade/result/HR."""
        grade, result, avg_hr, max_hr = _climb_fields(
            {"split_type": "interval_active", 15: 140, 16: 160}
        )
        assert grade is None
        assert result is None
        assert avg_hr is None
        assert max_hr is None

    def test_a_climb_active_split_missing_the_fields_entirely_is_not_a_climb_route(self) -> None:
        """Real data confirms fields 70/71 are only ever present on a genuine bouldering
        climb_active split -- a plain run's own climb_active-shaped split (if the field name
        were ever reused for a non-climbing sport) must not fabricate a route."""
        grade, result, avg_hr, max_hr = _climb_fields({"split_type": "climb_active"})
        assert grade is None
        assert result is None
        assert avg_hr is None
        assert max_hr is None


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


def test_workout_absent_from_a_file_with_no_structured_workout() -> None:
    """The synthetic fixture has no workout_mesgs/workout_step_mesgs -- most activities don't;
    must parse cleanly with workout=None, not error."""
    batch = parse_fit(FIXTURE.read_bytes())
    assert batch.activity is not None
    assert batch.activity.workout is None


class TestParseWorkout:
    """Row shapes are direct transcriptions of a real structured-workout FIT file (a warmup, a
    5x-repeated [1km interval, 75s recovery] block, and a cooldown, each with a target pace
    range) -- see `_parse_workout`'s own docstring for the confirmation."""

    def test_extracts_name_from_the_garbled_wkt_name_list(self) -> None:
        # `wkt_name` decodes as a list of fragments on real files -- only the first is real.
        workout_row = {
            "capabilities": "tcx",
            "wkt_name": ["W9 Tue · 5x1km Threshold", "", "ng R"],
            "wkt_description": "Focus: Lactate threshold.",
            "num_valid_steps": 5,
            "sport": "running",
            "sub_sport": "generic",
        }
        workout, _ = _parse_workout(workout_row, [])
        assert workout is not None
        assert workout.name == "W9 Tue · 5x1km Threshold"
        assert workout.description == "Focus: Lactate threshold."

    def test_parses_a_time_based_speed_target_step(self) -> None:
        step = {
            "duration_value": 900000,
            "target_value": 0,
            "custom_target_value_low": 2439,
            "custom_target_value_high": 2597,
            "secondary_target_value": 0,
            "message_index": 0,
            "duration_type": "time",
            "target_type": "speed",
            "intensity": "warmup",
            "duration_time": 900.0,
            "target_speed_zone": 0,
            "custom_target_speed_low": 2.439,
            "custom_target_speed_high": 2.597,
        }
        workout, _ = _parse_workout(None, [step])
        assert workout is not None
        assert len(workout.steps) == 1
        s = workout.steps[0]
        assert s.step_index == 0
        assert s.duration_type == "time"
        assert s.duration_time_s == 900.0
        assert s.duration_distance_m is None
        assert s.target_type == "speed"
        assert s.target_low_mps == 2.439
        assert s.target_high_mps == 2.597
        assert s.intensity == "warmup"
        assert s.repeat_from_step is None
        assert s.repeat_count is None

    def test_parses_a_distance_based_step(self) -> None:
        step = {
            "duration_value": 100000,
            "message_index": 1,
            "duration_type": "distance",
            "target_type": "speed",
            "intensity": "active",
            "duration_distance": 1000.0,
            "custom_target_speed_low": 3.226,
            "custom_target_speed_high": 3.333,
        }
        workout, _ = _parse_workout(None, [step])
        assert workout is not None
        s = workout.steps[0]
        assert s.duration_type == "distance"
        assert s.duration_distance_m == 1000.0
        assert s.duration_time_s is None
        assert s.target_low_mps == 3.226
        assert s.target_high_mps == 3.333

    def test_parses_a_repeat_step(self) -> None:
        step = {
            "duration_value": 1,
            "target_value": 5,
            "message_index": 3,
            "duration_type": "repeat_until_steps_cmplt",
            "duration_step": 1,
            "repeat_steps": 5,
        }
        workout, _ = _parse_workout(None, [step])
        assert workout is not None
        s = workout.steps[0]
        assert s.duration_type == "repeat_until_steps_cmplt"
        assert s.repeat_from_step == 1
        assert s.repeat_count == 5
        # A repeat step carries no target/intensity of its own.
        assert s.target_type is None
        assert s.intensity is None

    def test_does_not_extract_a_target_range_for_a_non_speed_target_type(self) -> None:
        """Only target_type == "speed" has a real-data-confirmed extraction (see module
        docstring) -- a heart_rate-targeted step must not get a fabricated pace range."""
        step = {
            "message_index": 0,
            "duration_type": "time",
            "target_type": "heart_rate",
            "custom_target_value_low": 120,
            "custom_target_value_high": 140,
        }
        workout, _ = _parse_workout(None, [step])
        assert workout is not None
        s = workout.steps[0]
        assert s.target_type == "heart_rate"
        assert s.target_low_mps is None
        assert s.target_high_mps is None

    def test_steps_are_ordered_by_message_index_regardless_of_input_order(self) -> None:
        steps = [
            {"message_index": 2, "duration_type": "time", "duration_time_s": 3},
            {"message_index": 0, "duration_type": "time", "duration_time_s": 1},
            {"message_index": 1, "duration_type": "time", "duration_time_s": 2},
        ]
        workout, _ = _parse_workout(None, steps)
        assert workout is not None
        assert [s.step_index for s in workout.steps] == [0, 1, 2]

    def test_unmapped_step_field_becomes_a_per_step_extra_metric_not_dropped(self) -> None:
        """A field on a step row that isn't part of the small core-fields mapping (here, an
        SDK-unmapped numeric key, as seen on a real repeat step) must still be cataloged --
        never silently discarded, same "never drop a field" rule as every other message type."""
        step = {"message_index": 3, "duration_type": "repeat_until_steps_cmplt", 18: 0}
        _, extra_metrics = _parse_workout(None, [step])
        keys = {m.key for m in extra_metrics}
        # `_message_short_name` strips the "_mesgs" suffix from the message-type-derived prefix.
        assert "fit.workout_step_3.18" in keys

    def test_returns_none_when_there_is_no_workout_data_at_all(self) -> None:
        workout, extra_metrics = _parse_workout(None, [])
        assert workout is None
        assert extra_metrics == []
