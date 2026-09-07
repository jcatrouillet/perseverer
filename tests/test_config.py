"""Tests for config.py::Settings.garmin_tokenstore_dir_for -- each athlete must resolve to its
own, distinct token-store directory (see the second-athlete work: this used to be one shared
global directory for every athlete)."""

from pathlib import Path

from perseverer.config import Settings


def test_different_athletes_get_different_tokenstore_dirs(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    a = settings.garmin_tokenstore_dir_for("athlete-a")
    b = settings.garmin_tokenstore_dir_for("athlete-b")
    assert a != b
    assert a == tmp_path / "garmin_tokens" / "athlete-a"
    assert b == tmp_path / "garmin_tokens" / "athlete-b"
