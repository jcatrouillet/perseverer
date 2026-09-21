"""Request/response models for POST/GET /notes -- the agent-writable write path AGENTS.md's
mission statement calls for. Scoped to activities, days, and weeks for now; a new entity_type is
a data-only addition, not a schema change. See docs/adr/0006-phase-3-read-api-and-rollups.md
decision 7.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EntityType = Literal["activity", "day", "week"]


class NoteCreate(BaseModel):
    entity_type: EntityType
    # An activity ULID when entity_type="activity"; an ISO local_date ("2026-08-02") when
    # entity_type="day"; that same week's Monday local_date when entity_type="week" (matching
    # rollups.py::week_start_monday -- the same convention WeekView.tsx already keys its own
    # `weekRange().start` on, so a week's note lives at the one date every other week-scoped
    # view already agrees is "this week").
    entity_id: str
    body: str
    author: str | None = None


class NoteUpdate(BaseModel):
    # Only the text is editable -- entity_type/entity_id aren't (a note doesn't move to a
    # different day/week/activity, it's deleted and re-created there if that's really what's
    # meant), and author is set once at creation and otherwise left alone.
    body: str


class NoteOut(BaseModel):
    id: int
    entity_type: str
    entity_id: str
    body: str
    author: str | None
    created_at: datetime
    updated_at: datetime
