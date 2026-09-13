"""Request/response models for /blood-tests. See db/schema.py::blood_test_result for the storage
shape -- one row per marker per draw, several rows sharing one local_date forming one logical
panel. Reference ranges are the athlete's own, entered from their lab report; never a canonical
"normal range" this project asserts.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, field_validator, model_validator


def _valid_iso_date(v: str) -> str:
    try:
        date.fromisoformat(v)
    except ValueError as e:
        raise ValueError("local_date must be an ISO date (YYYY-MM-DD)") from e
    return v


def _marker_not_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("marker must not be blank")
    return v


def _valid_reference_range(low: float | None, high: float | None) -> None:
    if low is not None and high is not None and low > high:
        raise ValueError("reference_low must not exceed reference_high")


class BloodTestResultIn(BaseModel):
    local_date: str  # ISO date -- the draw date
    marker: str  # e.g. "LDL Cholesterol" -- the athlete's own label, not a fixed catalog
    value_num: float
    unit: str | None = None  # e.g. "mg/dL"
    reference_low: float | None = None  # the athlete's own lab-reported range; either may be null
    reference_high: float | None = None
    lab_name: str | None = None
    notes: str | None = None

    @field_validator("local_date")
    @classmethod
    def _date(cls, v: str) -> str:
        return _valid_iso_date(v)

    @field_validator("marker")
    @classmethod
    def _marker(cls, v: str) -> str:
        return _marker_not_blank(v)

    @model_validator(mode="after")
    def _range(self) -> BloodTestResultIn:
        _valid_reference_range(self.reference_low, self.reference_high)
        return self


class BloodTestMarkerIn(BaseModel):
    """One marker within a `BloodTestBatchIn` -- the shared local_date/lab_name/notes live on the
    batch itself, not repeated per marker."""

    marker: str
    value_num: float
    unit: str | None = None
    reference_low: float | None = None
    reference_high: float | None = None

    @field_validator("marker")
    @classmethod
    def _marker(cls, v: str) -> str:
        return _marker_not_blank(v)

    @model_validator(mode="after")
    def _range(self) -> BloodTestMarkerIn:
        _valid_reference_range(self.reference_low, self.reference_high)
        return self


class BloodTestBatchIn(BaseModel):
    """A whole panel entered at once -- one draw date, any number of markers."""

    local_date: str
    lab_name: str | None = None
    notes: str | None = None
    results: list[BloodTestMarkerIn]

    @field_validator("local_date")
    @classmethod
    def _date(cls, v: str) -> str:
        return _valid_iso_date(v)

    @model_validator(mode="after")
    def _at_least_one_marker(self) -> BloodTestBatchIn:
        if not self.results:
            raise ValueError("results must include at least one marker")
        return self


class BloodTestResultOut(BaseModel):
    id: int
    local_date: str
    marker: str
    value_num: float
    unit: str | None
    reference_low: float | None
    reference_high: float | None
    lab_name: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
