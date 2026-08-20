"""timezone_lookup.py tests -- real-world coordinates with known IANA zones, confirming both the
zone lookup itself and that DST is resolved correctly for the specific UTC instant given (not a
fixed year-round offset)."""

from __future__ import annotations

from datetime import UTC, datetime

from sporthealth.timezone_lookup import offset_from_coordinates


def test_bay_area_coordinates_resolve_to_los_angeles_offset() -> None:
    # The real activity that surfaced this bug: 2022-08-26T00:03:18Z at (37.412418,
    # -121.999022), San Jose CA -- August is PDT (UTC-7), which is what pushes this run onto
    # 2022-08-25 local, not the raw UTC calendar date of 2022-08-26.
    offset_s, tz_name = offset_from_coordinates(
        37.412418, -121.999022, datetime(2022, 8, 26, 0, 3, 18)
    )
    assert tz_name == "America/Los_Angeles"
    assert offset_s == -7 * 3600


def test_same_zone_resolves_different_offset_across_a_dst_boundary() -> None:
    # Same coordinates, but a date outside DST -- PST is UTC-8, not UTC-7. Proves the offset is
    # computed from the specific instant, not memoized/fixed per zone.
    offset_s, _ = offset_from_coordinates(37.412418, -121.999022, datetime(2022, 1, 15, 12, 0, 0))
    assert offset_s == -8 * 3600


def test_paris_coordinates_resolve_to_europe_paris() -> None:
    offset_s, tz_name = offset_from_coordinates(48.8566, 2.3522, datetime(2023, 6, 1, 10, 0, 0))
    assert tz_name == "Europe/Paris"
    assert offset_s == 2 * 3600  # CEST


def test_tokyo_coordinates_resolve_to_asia_tokyo_no_dst() -> None:
    offset_s, tz_name = offset_from_coordinates(35.6762, 139.6503, datetime(2023, 1, 1, 0, 0, 0))
    assert tz_name == "Asia/Tokyo"
    assert offset_s == 9 * 3600


def test_timezone_aware_datetime_is_accepted() -> None:
    offset_s, tz_name = offset_from_coordinates(
        35.6762, 139.6503, datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
    )
    assert tz_name == "Asia/Tokyo"
    assert offset_s == 9 * 3600


def test_open_ocean_coordinates_resolve_to_a_nautical_zone() -> None:
    # Mid-Pacific, nowhere near any landmass -- timezonefinder still resolves this to one of
    # its nautical Etc/GMT zones (it covers the whole globe, not just populated areas), so this
    # is a real, non-None result, just not a populated-place IANA name.
    offset_s, tz_name = offset_from_coordinates(0.0, -150.0, datetime(2023, 1, 1))
    assert tz_name == "Etc/GMT+10"
    assert offset_s == -10 * 3600


def test_out_of_range_coordinates_fall_back_to_zero() -> None:
    offset_s, tz_name = offset_from_coordinates(999.0, 999.0, datetime(2023, 1, 1))
    assert offset_s == 0
    assert tz_name is None
