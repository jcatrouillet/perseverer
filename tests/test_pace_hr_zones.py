"""Tests for pace_hr_zones.compute_pace_hr_zones: the windowed (not all-time, not
performance_daily_rollup's own 42-day rolling) VDOT/max-HR profile, the five zone pace boundaries
derived from it, and the empirical-first/formula-fallback HR range per zone.
"""

import datetime as dt
from pathlib import Path

from sqlalchemy import Connection, Engine

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_metric, athlete, metadata, metric_definition
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.pace_hr_zones import (
    MAX_HR_WINDOW_DAYS,
    MAX_SAMPLE_RUNS_PER_ZONE,
    MIN_ZONE_HR_SAMPLES,
    RACE_WINDOW_DAYS,
    TRAINING_RUN_WINDOW_DAYS,
    ZONE_LABELS,
    compute_pace_hr_zones,
)
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.vdot import compute_threshold_pace_s_per_km

_AVG_HR_KEY = "fit.session.avg_heart_rate"
_MAX_HR_KEY = "fit.session.max_heart_rate"
_AS_OF = dt.date(2026, 6, 15)


def _days_ago(days: int) -> str:
    return (_AS_OF - dt.timedelta(days=days)).isoformat()


# A date safely inside every window (training run / race / max HR).
_RECENT = _days_ago(30)
# A date inside the race/max-HR window (730 days) but outside the training-run window (365 days).
_MID_RANGE = _days_ago(TRAINING_RUN_WINDOW_DAYS + 30)
# A date outside every window.
_TOO_OLD = _days_ago(RACE_WINDOW_DAYS + 30)


def _engine(tmp_path: Path, *, birthdate: str | None = None) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                birthdate=birthdate,
                created_at=now,
            )
        )
        for key in (VDOT_METRIC_KEY, AVG_GAP_METRIC_KEY, _AVG_HR_KEY, _MAX_HR_KEY):
            conn.execute(
                metric_definition.insert().values(
                    metric_key=key,
                    display_name=key,
                    category="performance",
                    value_type="numeric",
                    first_seen_at=now,
                    first_seen_source="perseverer",
                )
            )
        conn.commit()
    return engine


def _add_run(
    conn: Connection,
    *,
    activity_id: str,
    local_date: str,
    vdot: float,
    gap_speed_mps: float | None = None,
    avg_hr: float | None = None,
    max_hr: float | None = None,
    sport: str = "running",
    is_race: bool = False,
) -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date=local_date,
            name="Run",
            sport=sport,
            is_race=is_race,
            duration_s=1800.0,
            moving_duration_s=1700.0,
            distance_m=5000.0,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )

    def _metric(metric_key: str, value: float) -> None:
        conn.execute(
            activity_metric.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                metric_key=metric_key,
                value_num=value,
                source="perseverer",
                created_at=now,
            )
        )

    _metric(VDOT_METRIC_KEY, vdot)
    if gap_speed_mps is not None:
        _metric(AVG_GAP_METRIC_KEY, gap_speed_mps)
    if avg_hr is not None:
        _metric(_AVG_HR_KEY, avg_hr)
    if max_hr is not None:
        _metric(_MAX_HR_KEY, max_hr)


def test_no_data_reports_the_gap(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)
    assert result.profile_vdot is None
    assert result.profile_vdot_source is None
    assert result.profile_max_hr_bpm is None
    assert len(result.zones) == 5
    assert [z.label for z in result.zones] == list(ZONE_LABELS)
    assert all(z.hr_low_bpm is None and z.hr_high_bpm is None for z in result.zones)
    assert any("No qualifying run" in m for m in result.missing)
    assert any("No heart rate data" in m for m in result.missing)


def test_training_run_vdot_ignores_a_stronger_run_outside_the_training_window(
    tmp_path: Path,
) -> None:
    """A strong training run older than TRAINING_RUN_WINDOW_DAYS must lose to a weaker, more
    recent one -- this profile tracks current fitness within a bounded window, not an all-time
    peak (an earlier version used an unbounded lookback; revised after the athlete pointed out
    that anchoring the whole table to a years-old effort was the real problem). Neither run is
    marked as a race, so this also exercises the training-run fallback path."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_run(conn, activity_id="strong-too-old", local_date=_TOO_OLD, vdot=55.0, avg_hr=175.0)
        _add_run(conn, activity_id="weak-recent", local_date=_RECENT, vdot=40.0, avg_hr=140.0)
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)
    assert result.profile_vdot == 40.0
    assert result.profile_vdot_activity is not None
    assert result.profile_vdot_activity.activity_id == "weak-recent"
    assert result.profile_vdot_source == "training_run"
    assert any("marked as a race" in m for m in result.missing)


def test_profile_vdot_prefers_a_marked_race_over_a_faster_training_run(tmp_path: Path) -> None:
    """A real bug this fixes: an all-out training segment (a track rep, a strides set) can post a
    higher VDOT than the athlete's own best real race, since the Daniels formula is calibrated
    against genuine race efforts, not short training bursts. Once at least one race is marked, it
    must win over any faster non-race training run, however much higher that run's own VDOT is --
    even one more recent than the race, and even though the race here is old enough to have
    already fallen outside the (shorter) training-run window, which is exactly why the race window
    is longer."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_run(conn, activity_id="hard-training-segment", local_date=_RECENT, vdot=55.0)
        _add_run(conn, activity_id="real-race", local_date=_MID_RANGE, vdot=44.5, is_race=True)
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)
    assert result.profile_vdot == 44.5
    assert result.profile_vdot_activity is not None
    assert result.profile_vdot_activity.activity_id == "real-race"
    assert result.profile_vdot_source == "race"
    assert not any("marked as a race" in m for m in result.missing)


def test_race_vdot_ignores_a_race_outside_the_race_window(tmp_path: Path) -> None:
    """A race older than RACE_WINDOW_DAYS is out of scope entirely -- the fallback path applies
    exactly as if no race existed at all."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_run(conn, activity_id="too-old-race", local_date=_TOO_OLD, vdot=44.5, is_race=True)
        _add_run(conn, activity_id="recent-training", local_date=_RECENT, vdot=38.0)
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)
    assert result.profile_vdot == 38.0
    assert result.profile_vdot_activity is not None
    assert result.profile_vdot_activity.activity_id == "recent-training"
    assert result.profile_vdot_source == "training_run"


def test_profile_vdot_picks_the_best_among_several_marked_races(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_run(conn, activity_id="race-weak", local_date=_MID_RANGE, vdot=33.2, is_race=True)
        _add_run(conn, activity_id="race-best", local_date=_days_ago(400), vdot=44.5, is_race=True)
        _add_run(conn, activity_id="race-mid", local_date=_RECENT, vdot=42.1, is_race=True)
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)
    assert result.profile_vdot == 44.5
    assert result.profile_vdot_activity is not None
    assert result.profile_vdot_activity.activity_id == "race-best"
    assert result.profile_vdot_source == "race"


def test_pace_boundaries_match_vdot_module_fractions(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_run(conn, activity_id="a0", local_date=_RECENT, vdot=50.0)
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)

    zone2, zone3, zone4, zone5 = result.zones[1], result.zones[2], result.zones[3], result.zones[4]
    # Zone 2/3 boundary is the aerobic threshold (0.73 VO2max) -- zone 2's fast edge and zone 3's
    # slow edge are the same pace.
    aerobic_pace = compute_threshold_pace_s_per_km(50.0, 0.73)
    assert zone2.pace_fast_s_per_km == aerobic_pace
    assert zone3.pace_slow_s_per_km == aerobic_pace
    # Zone 4/5 boundary is the lactate threshold (0.88 VO2max).
    anaerobic_pace = compute_threshold_pace_s_per_km(50.0, 0.88)
    assert zone4.pace_fast_s_per_km == anaerobic_pace
    assert zone5.pace_slow_s_per_km == anaerobic_pace
    # Zone 5 has no defined fast-side ceiling; zone 1 has no defined slow-side floor.
    assert zone5.pace_fast_s_per_km is None
    assert result.zones[0].pace_slow_s_per_km is None
    # Zones 2-4 (the ones with a defined pace band on both sides) get progressively faster.
    assert zone2.pace_slow_s_per_km is not None
    assert zone3.pace_slow_s_per_km is not None
    assert zone4.pace_slow_s_per_km is not None
    paces_slow: list[float] = [
        zone2.pace_slow_s_per_km,
        zone3.pace_slow_s_per_km,
        zone4.pace_slow_s_per_km,
    ]
    assert paces_slow == sorted(paces_slow, reverse=True)


def test_empirical_hr_used_once_enough_runs_qualify_in_a_zone(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        # A strong-effort run sets a high profile VDOT, with a wide easy-pace GAP so the recovery
        # zone (well below threshold) has real, distinct runs to qualify against.
        _add_run(conn, activity_id="best", local_date=_RECENT, vdot=50.0, avg_hr=180.0)
        # Recovery-pace runs: slow GAP speed puts these in Zone 1. gap_speed_mps chosen well below
        # the zone1/2 boundary pace for vdot=50. Dated within the training-run window (unlike the
        # profile-setting run above, these must also fall inside TRAINING_RUN_WINDOW_DAYS to be
        # counted as qualifying evidence).
        for i, hr in enumerate([120.0, 122.0, 124.0, 126.0, 128.0]):
            _add_run(
                conn,
                activity_id=f"recovery{i}",
                local_date=_days_ago(60 + i),
                vdot=30.0,
                gap_speed_mps=2.0,  # ~8:20/km, deep in recovery territory for a VDOT-50 athlete
                avg_hr=hr,
            )
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)

    zone1 = result.zones[0]
    assert zone1.qualifying_run_count == 5
    assert zone1.hr_source == "empirical"
    assert zone1.hr_low_bpm is not None and zone1.hr_high_bpm is not None
    assert zone1.hr_low_bpm < zone1.hr_high_bpm
    assert len(zone1.sample_runs) == 5


def test_formula_fallback_used_below_the_minimum_sample_count(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_run(conn, activity_id="best", local_date=_RECENT, vdot=50.0, max_hr=190.0)
        # Only two recovery-pace runs -- one short of MIN_ZONE_HR_SAMPLES.
        assert MIN_ZONE_HR_SAMPLES == 3
        for i in range(2):
            _add_run(
                conn,
                activity_id=f"recovery{i}",
                local_date=_days_ago(60 + i),
                vdot=30.0,
                gap_speed_mps=2.0,
                avg_hr=120.0,
            )
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)

    zone1 = result.zones[0]
    assert zone1.qualifying_run_count == 2
    assert zone1.hr_source == "formula_fallback"
    assert zone1.hr_high_bpm is not None
    # Zone 1's HR fraction is 0.65 of max HR (190 bpm here) -- Daniels' own Easy-pace HR floor.
    assert 118 < zone1.hr_high_bpm < 129


def test_max_hr_prefers_empirical_over_birthdate_formula(tmp_path: Path) -> None:
    engine = _engine(tmp_path, birthdate="1990-01-01")
    with engine.connect() as conn:
        _add_run(conn, activity_id="a0", local_date=_MID_RANGE, vdot=45.0, max_hr=195.0)
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)
    assert result.profile_max_hr_bpm == 195.0
    assert result.profile_max_hr_source == "empirical"


def test_max_hr_ignores_an_empirical_reading_outside_its_own_window(tmp_path: Path) -> None:
    engine = _engine(tmp_path, birthdate="1990-01-01")
    with engine.connect() as conn:
        too_old_for_max_hr = _days_ago(MAX_HR_WINDOW_DAYS + 30)
        _add_run(conn, activity_id="a0", local_date=too_old_for_max_hr, vdot=45.0, max_hr=195.0)
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)
    assert result.profile_max_hr_source == "formula_fallback"


def test_max_hr_falls_back_to_tanaka_formula_with_a_birthdate_and_no_empirical_data(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, birthdate="1990-01-01")
    with engine.connect() as conn:
        _add_run(conn, activity_id="a0", local_date=_RECENT, vdot=45.0)
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)
    assert result.profile_max_hr_source == "formula_fallback"
    age = (_AS_OF - dt.date(1990, 1, 1)).days / 365.25
    assert result.profile_max_hr_bpm == 208.0 - 0.7 * age


def test_sample_runs_capped_and_span_the_full_hr_range(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_run(conn, activity_id="best", local_date=_RECENT, vdot=50.0)
        n = MAX_SAMPLE_RUNS_PER_ZONE + 8
        for i in range(n):
            _add_run(
                conn,
                activity_id=f"recovery{i}",
                local_date=_days_ago(i + 1),
                vdot=30.0,
                gap_speed_mps=2.0,
                avg_hr=100.0 + i,  # a distinct, evenly-spread HR per run
            )
        conn.commit()
        result = compute_pace_hr_zones(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=_AS_OF)

    zone1 = result.zones[0]
    assert zone1.qualifying_run_count == n
    assert len(zone1.sample_runs) == MAX_SAMPLE_RUNS_PER_ZONE
    sample_hrs = [r.avg_hr_bpm for r in zone1.sample_runs]
    assert sample_hrs == sorted(sample_hrs)
    assert sample_hrs[0] == 100.0
    assert sample_hrs[-1] == 100.0 + n - 1
