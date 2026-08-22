"""Response model for GET /activities/{id}/stream -- columnar, not row-of-dicts, matching
`streams.py`'s own `dict[str, list]` shape and keeping the JSON payload smaller."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StreamResponse(BaseModel):
    activity_id: str
    tier: str
    channels: list[str]
    timestamps: list[datetime]
    series: dict[str, list[float | None]]
