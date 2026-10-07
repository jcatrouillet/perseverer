"""Tests for health/fit_parser.py: timestamp_16 reconstruction and message extraction.

Uses a small synthetic fixture built with garmin_fit_sdk's own Encoder (see
tests/fixtures/fit/synthetic_health.fit), same technique as fit/test_parser.py's
synthetic_run.fit — no real personal monitoring data is committed.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from perseverer.health.fit_parser import (
    FIT_EPOCH,
    _resolve_compressed_timestamps,
    parse_health_fit,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fit" / "synthetic_health.fit"


# --- _resolve_compressed_timestamps: directly tested per docs/ARCHITECTURE.md -----------------


def test_full_timestamp_rows_pass_through_unchanged() -> None:
    ts = datetime(2025, 6, 1, tzinfo=UTC)
    rows = [{"timestamp": ts, "heart_rate": 60}]
    resolved = _resolve_compressed_timestamps(rows, seed_timestamp=None)
    assert resolved[0]["timestamp"] == ts
    assert "timestamp_16" not in resolved[0]


def test_timestamp_16_reconstructed_against_seed() -> None:
    seed = datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
    target = datetime(2025, 6, 1, 0, 5, 0, tzinfo=UTC)
    t16 = int((target - FIT_EPOCH).total_seconds()) & 0xFFFF
    rows = [{"timestamp_16": t16, "heart_rate": 60}]

    resolved = _resolve_compressed_timestamps(rows, seed_timestamp=seed)

    assert resolved[0]["timestamp"] == target
    assert "timestamp_16" not in resolved[0]


def test_timestamp_16_chains_off_a_previously_resolved_row() -> None:
    seed = datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
    t1 = datetime(2025, 6, 1, 0, 1, 0, tzinfo=UTC)
    t2 = datetime(2025, 6, 1, 0, 2, 0, tzinfo=UTC)
    rows = [
        {"timestamp_16": int((t1 - FIT_EPOCH).total_seconds()) & 0xFFFF, "heart_rate": 60},
        {"timestamp_16": int((t2 - FIT_EPOCH).total_seconds()) & 0xFFFF, "heart_rate": 62},
    ]

    resolved = _resolve_compressed_timestamps(rows, seed_timestamp=seed)

    assert resolved[0]["timestamp"] == t1
    assert resolved[1]["timestamp"] == t2


def test_timestamp_16_rollover_is_handled() -> None:
    """The 16-bit counter wraps every 65536s (~18.2h). A row just past a wrap boundary from the
    seed has a *smaller* raw timestamp_16 than the seed's low 16 bits -- reconstruction must add
    a full 0x10000, not silently go backwards."""
    base = 1_000_000_000 - (1_000_000_000 % 0x10000)  # a clean multiple of 0x10000
    seed_epoch_s = base + 65530  # low-16 bits = 65530, close to the wrap boundary
    target_epoch_s = seed_epoch_s + 12  # crosses the boundary: low-16 bits wrap to 6
    seed = FIT_EPOCH + timedelta(seconds=seed_epoch_s)
    t16 = target_epoch_s & 0xFFFF
    assert t16 < (seed_epoch_s & 0xFFFF)  # precondition: this row's raw value looks "earlier"

    rows = [{"timestamp_16": t16, "heart_rate": 60}]
    resolved = _resolve_compressed_timestamps(rows, seed_timestamp=seed)

    assert resolved[0]["timestamp"] == FIT_EPOCH + timedelta(seconds=target_epoch_s)


def test_no_seed_and_no_full_timestamp_leaves_row_unresolved() -> None:
    """A timestamp_16-only row with nothing to reconstruct against is dropped, not guessed."""
    rows = [{"timestamp_16": 123, "heart_rate": 60}]
    resolved = _resolve_compressed_timestamps(rows, seed_timestamp=None)
    assert "timestamp" not in resolved[0]
    assert "timestamp_16" in resolved[0]  # left alone, not silently consumed


# --- parse_health_fit: end-to-end against the synthetic fixture ------------------------------


def test_monitoring_heart_rate_stream_uses_reconstructed_timestamps() -> None:
    """Both monitoring rows in the fixture are timestamp_16-only, seeded from
    monitoring_info_mesgs -- this is the majority case in real WELLNESS files."""
    batch = parse_health_fit(FIXTURE.read_bytes())
    hr_points = sorted(
        (p for p in batch.stream_points if p.metric_key == "heart_rate"),
        key=lambda p: p.timestamp_utc,
    )
    assert len(hr_points) == 2
    assert hr_points[0].timestamp_utc == datetime(2025, 6, 1, 22, 1, 0, tzinfo=UTC)
    assert hr_points[0].value == 60.0
    assert hr_points[1].timestamp_utc == datetime(2025, 6, 1, 22, 2, 0, tzinfo=UTC)
    assert hr_points[1].value == 62.0


def test_simple_streams_extracted() -> None:
    batch = parse_health_fit(FIXTURE.read_bytes())
    by_key = {p.metric_key: p.value for p in batch.stream_points}
    assert by_key["stress_level"] == 25.0
    assert by_key["respiration_rate"] == 14.5
    assert by_key["spo2"] == 97.0
    assert by_key["hrv"] == 50.0


def test_resting_heart_rate_observation() -> None:
    batch = parse_health_fit(FIXTURE.read_bytes())
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["resting_heart_rate"].value_num == 48.0
    assert obs["resting_heart_rate"].unit == "bpm"


def test_hrv_status_summary_named_and_status_observations() -> None:
    batch = parse_health_fit(FIXTURE.read_bytes())
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["hrv.weekly_average"].value_num == 55.0
    assert obs["hrv.last_night_average"].value_num == 60.0
    assert obs["hrv.last_night_5_min_high"].value_num == 72.0
    assert obs["hrv.status"].value_text == "balanced"


def test_sleep_session_and_stages_derived_from_sleep_level_rows() -> None:
    batch = parse_health_fit(FIXTURE.read_bytes())
    assert len(batch.sleep_sessions) == 1
    session = batch.sleep_sessions[0]
    assert session.local_date == "2025-06-01"
    assert session.start_time_utc == datetime(2025, 6, 1, 23, 0, 0, tzinfo=UTC)
    assert session.end_time_utc == datetime(2025, 6, 2, 3, 0, 0, tzinfo=UTC)
    assert session.total_sleep_s == 4 * 3600
    assert session.sleep_score == 75.0

    stages = sorted(session.stages, key=lambda s: s.start_time_utc)
    assert [s.stage for s in stages] == ["light", "deep"]
    assert stages[0].start_time_utc == datetime(2025, 6, 1, 23, 0, 0, tzinfo=UTC)
    assert stages[0].end_time_utc == datetime(2025, 6, 2, 1, 0, 0, tzinfo=UTC)
    assert stages[1].end_time_utc == datetime(2025, 6, 2, 3, 0, 0, tzinfo=UTC)


def test_sleep_assessment_scores_become_generic_observations() -> None:
    """Every sleep_assessment_mesgs score maps to sleep.<field> -- see docs/ARCHITECTURE.md
    (no dedicated multi-score sleep table)."""
    batch = parse_health_fit(FIXTURE.read_bytes())
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["sleep.combined_awake_score"].value_num == 80.0
    assert obs["sleep.deep_sleep_score"].value_num == 70.0
    assert obs["sleep.rem_sleep_score"].value_num == 60.0
    assert obs["sleep.average_stress_during_sleep"].value_num == 30.0
    # overall_sleep_score is on the session row itself, not duplicated as an observation
    assert "sleep.overall_sleep_score" not in obs


def test_nap_event_becomes_interval_observation() -> None:
    batch = parse_health_fit(FIXTURE.read_bytes())
    nap_obs = next(o for o in batch.observations if o.metric_key == "nap")
    assert nap_obs.aggregation == "interval"
    assert nap_obs.value_num == 20 * 60  # 20-minute nap, in seconds
    assert nap_obs.value_text == "ideal_duration_low_need"
    assert nap_obs.interval_start == datetime(2025, 6, 2, 4, 0, 0, tzinfo=UTC)
    assert nap_obs.interval_end == datetime(2025, 6, 2, 4, 20, 0, tzinfo=UTC)


def test_unrecognized_message_type_is_cataloged_not_dropped() -> None:
    """device_settings_mesgs is a real, in-profile message type this parser doesn't model --
    its fields must still surface via unrecognized_field_keys."""
    batch = parse_health_fit(FIXTURE.read_bytes())
    assert "fit.device_settings.active_time_zone" in batch.unrecognized_field_keys
    assert "fit.device_settings.utc_offset" in batch.unrecognized_field_keys
