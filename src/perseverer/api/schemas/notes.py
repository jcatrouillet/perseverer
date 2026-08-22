"""Request/response models for POST/GET /notes -- the agent-writable write path CLAUDE.md's
mission statement calls for. Scoped to activities and days only for now; a new entity_type is
a data-only addition, not a schema change. See docs/adr/0006-phase-3-read-api-and-rollups.md
decision 7.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EntityType = Literal["activity", "day"]


class NoteCreate(BaseModel):
    entity_type: EntityType
    # An activity ULID when entity_type="activity", an ISO local_date ("2026-08-02") when
    # entity_type="day".
    entity_id: str
    body: str
    author: str | None = None


class NoteOut(BaseModel):
    id: int
    entity_type: str
    entity_id: str
    body: str
    author: str | None
    created_at: datetime
    updated_at: datetime
