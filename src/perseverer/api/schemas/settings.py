"""Request/response models for GET/PUT /settings/hr-zones -- an athlete's own configured HR
training zones, independent of the per-activity, device-reported zones a watch bakes into its
own FIT data. See db/schema.py::athlete_hr_zone_config and hr_zones.py for the storage shape,
the blended-formula rationale, and why it's three reference points (max/threshold/resting HR),
not the four zone boundaries directly.

Also GET/PUT /settings/running-load -- an athlete's own configured running threshold pace, the
one calibration constant running_load.py::compute_running_tss needs. See
db/schema.py::athlete_running_load_config, which this mirrors deliberately.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, model_validator

from perseverer.hr_zones import compute_hr_zone_boundaries

_MIN_HEIGHT_CM = 50.0
_MAX_HEIGHT_CM = 250.0
_MAX_PLAUSIBLE_AGE_YEARS = 120


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


class RunningLoadConfigIn(BaseModel):
    threshold_pace_sec_per_km: float | None = None

    @model_validator(mode="after")
    def _positive_if_set(self) -> RunningLoadConfigIn:
        if self.threshold_pace_sec_per_km is not None and self.threshold_pace_sec_per_km <= 0:
            raise ValueError("threshold_pace_sec_per_km must be positive")
        return self


class RunningLoadConfigOut(BaseModel):
    threshold_pace_sec_per_km: float | None


# GET/PUT /settings/profile -- optional athlete profile facts used only as inputs to
# formula-based FALLBACKS elsewhere (max HR: performance_rollup.py; BMR:
# api/routers/health.py::get_health_dashboard) when there isn't enough empirical/device data yet.
# See db/schema.py::athlete's own birthdate/height_cm/sex columns.
class AthleteProfileIn(BaseModel):
    birthdate: str | None = None
    height_cm: float | None = None
    sex: str | None = None

    @model_validator(mode="after")
    def _sane_values(self) -> AthleteProfileIn:
        if self.birthdate is not None:
            try:
                parsed = date.fromisoformat(self.birthdate)
            except ValueError as e:
                raise ValueError("birthdate must be an ISO date (YYYY-MM-DD)") from e
            if parsed > date.today():
                raise ValueError("birthdate cannot be in the future")
            if (date.today() - parsed).days / 365.25 > _MAX_PLAUSIBLE_AGE_YEARS:
                raise ValueError(f"birthdate implies an age over {_MAX_PLAUSIBLE_AGE_YEARS} years")
        if self.height_cm is not None and not (_MIN_HEIGHT_CM <= self.height_cm <= _MAX_HEIGHT_CM):
            raise ValueError(f"height_cm must be between {_MIN_HEIGHT_CM} and {_MAX_HEIGHT_CM}")
        if self.sex is not None and self.sex not in ("male", "female"):
            raise ValueError("sex must be 'male' or 'female'")
        return self


class AthleteProfileOut(BaseModel):
    birthdate: str | None
    height_cm: float | None
    sex: str | None


# GET /settings/garmin/status
class GarminAuthStatusOut(BaseModel):
    token_store_present: bool
    token_store_age_days: int | None
    last_sync_status: str | None
    last_sync_at: datetime | None
    last_sync_error: str | None
    # Garmin exposes no readable expiry for the token that actually matters (the long-lived
    # refresh token -- the short-lived access token auto-refreshes silently and its own expiry
    # isn't actionable), so there's no real "expires at" to show. This is the practical
    # substitute: staleness.py::check_garmin_connect_staleness's own escalating warning/critical
    # signal, already computed daily by the worker -- null/null whenever the last sync
    # succeeded (nothing to warn about).
    staleness_severity: str | None
    staleness_message: str | None


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


# GET/POST/DELETE /settings/calendar-feed -- see calendar_feed.py's own module docstring.
class CalendarFeedStatusOut(BaseModel):
    enabled: bool
    created_at: str | None


class CalendarFeedUrlOut(BaseModel):
    url: str
