"""Response model for GET /fitness -- the independently-computed CTL/ATL/TSB series. See
docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
"""

from __future__ import annotations

from pydantic import BaseModel


class FitnessDailyRollupOut(BaseModel):
    local_date: str
    training_load: float
    ctl: float
    atl: float
    tsb: float
