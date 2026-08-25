"""Request/response models for GET/PUT /settings/hr-zones -- an athlete's own configured HR
training zones, independent of the per-activity, device-reported zones a watch bakes into its
own FIT data. See db/schema.py::athlete_hr_zone_config and hr_zones.py for the storage shape,
the blended-formula rationale, and why it's three reference points (max/threshold/resting HR),
not the four zone boundaries directly.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, model_validator

from perseverer.hr_zones import compute_hr_zone_boundaries


class HrZoneConfigIn(BaseModel):
    max_hr_bpm: float | None = None
    threshold_hr_bpm: float | None = None
    resting_hr_bpm: float | None = None

    @model_validator(mode="after")
    def _sane_relative_values(self) -> HrZoneConfigIn:
        if (
            self.max_hr_bpm is not None
            and self.resting_hr_bpm is not None
            and self.max_hr_bpm <= self.resting_hr_bpm
        ):
            raise ValueError("max_hr_bpm must be greater than resting_hr_bpm")
        if (
            self.max_hr_bpm is not None
            and self.threshold_hr_bpm is not None
            and self.threshold_hr_bpm > self.max_hr_bpm
        ):
            raise ValueError("threshold_hr_bpm cannot exceed max_hr_bpm")
        return self


class HrZoneConfigOut(BaseModel):
    max_hr_bpm: float | None
    threshold_hr_bpm: float | None
    resting_hr_bpm: float | None
    # Derived from the three fields above (hr_zones.py::compute_hr_zone_boundaries) -- null
    # unless all three are set. Included here so the frontend never needs its own copy of the
    # zone formula: one source of truth for the physiology.
    zone1_high_bpm: float | None
    zone2_high_bpm: float | None
    zone3_high_bpm: float | None
    zone4_high_bpm: float | None

    @classmethod
    def from_inputs(
        cls,
        max_hr_bpm: float | None,
        threshold_hr_bpm: float | None,
        resting_hr_bpm: float | None,
    ) -> HrZoneConfigOut:
        boundaries = compute_hr_zone_boundaries(max_hr_bpm, threshold_hr_bpm, resting_hr_bpm)
        return cls(
            max_hr_bpm=max_hr_bpm,
            threshold_hr_bpm=threshold_hr_bpm,
            resting_hr_bpm=resting_hr_bpm,
            zone1_high_bpm=boundaries[0] if boundaries else None,
            zone2_high_bpm=boundaries[1] if boundaries else None,
            zone3_high_bpm=boundaries[2] if boundaries else None,
            zone4_high_bpm=boundaries[3] if boundaries else None,
        )


# GET /settings/garmin/status
class GarminAuthStatusOut(BaseModel):
    token_store_present: bool
    token_store_age_days: int | None
    last_sync_status: str | None
    last_sync_at: datetime | None
    last_sync_error: str | None


# POST /settings/garmin/login
class GarminLoginIn(BaseModel):
    username: str
    password: str


# POST /settings/garmin/login response -- only ever returned with success=True; a failed
# attempt raises HTTPException instead (see api/routers/settings.py).
class GarminLoginOut(BaseModel):
    success: bool


# POST /settings/garmin/sync, POST /settings/rebuild, POST /settings/import/bulk-export -- all
# three return this immediately (the real work runs via BackgroundTasks); progress is polled
# via GET /settings/jobs/latest below.
class JobTriggerOut(BaseModel):
    triggered: bool


# GET /settings/jobs/latest -- the latest ingest_run row for one `source`, shared by the sync/
# rebuild/bulk-import triggers above rather than one status shape per action.
class JobStatusOut(BaseModel):
    source: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    items_seen: int
    items_new: int
    error_count: int
    first_error: str | None
