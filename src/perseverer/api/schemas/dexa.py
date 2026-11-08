"""Request/response models for /dexa-scans. See db/schema.py::dexa_scan_result: one row per
(region, metric) per scan, several rows sharing one local_date forming one scan. Values are SI
(masses in kg, volumes in cm3); the frontend converts for display.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, field_validator, model_validator


class DexaValueIn(BaseModel):
    region: str  # "total", "arms", "right_leg", "android"...
    metric: str  # "fat_pct", "lean_mass_kg", "bmd_g_cm2"...
    value_num: float

    @field_validator("region", "metric")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("region and metric must not be blank")
        return v


class DexaScanIn(BaseModel):
    """A whole scan entered at once: one date, any number of values. Re-posting the same date
    replaces the values it names (idempotent)."""

    local_date: str
    provider: str | None = None
    values: list[DexaValueIn]

    @field_validator("local_date")
    @classmethod
    def _date(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError as e:
            raise ValueError("local_date must be an ISO date (YYYY-MM-DD)") from e
        return v

    @model_validator(mode="after")
    def _at_least_one(self) -> DexaScanIn:
        if not self.values:
            raise ValueError("values must include at least one measurement")
        return self


class DexaValueOut(BaseModel):
    region: str
    metric: str
    value_num: float


class DexaScanOut(BaseModel):
    local_date: str
    provider: str | None
    values: list[DexaValueOut]
