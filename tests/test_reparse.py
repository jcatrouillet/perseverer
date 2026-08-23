"""Tests for reparse.py -- specifically that both FIT raw_object kinds this app has ever
archived under ("fit", the current unified kind, and "fit_activity", the Phase 1 kind still real
for anything ingested before that unification) parse identically. Caught by a real
activity_trim.py::clear_activity_trim call against a genuinely old activity in this athlete's own
archive failing with "raw bytes no longer parse as an activity" despite the bytes themselves
being perfectly fine -- reparse_raw_object simply didn't recognize the legacy kind label.
"""

from __future__ import annotations

from pathlib import Path

from perseverer.reparse import reparse_raw_object

FIXTURE = Path(__file__).parent / "fixtures" / "fit" / "synthetic_run.fit"


def test_fit_and_fit_activity_kinds_parse_identically() -> None:
    content = FIXTURE.read_bytes()

    fit_batch = reparse_raw_object("fit", content)
    legacy_batch = reparse_raw_object("fit_activity", content)

    assert fit_batch.kind == "activity"
    assert legacy_batch.kind == "activity"
    assert legacy_batch.activity is not None
    assert fit_batch.activity is not None
    assert legacy_batch.activity.distance_m == fit_batch.activity.distance_m
    assert legacy_batch.activity.duration_s == fit_batch.activity.duration_s
    assert legacy_batch.activity.start_time_utc == fit_batch.activity.start_time_utc


def test_unrecognized_kind_returns_unrecognized_batch() -> None:
    batch = reparse_raw_object("garmin_export_json", b"{}")
    assert batch.kind == "unrecognized"
    assert batch.activity is None
