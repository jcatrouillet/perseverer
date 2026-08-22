"""Request/response models for POST /auth/login (Phase 5). See
docs/adr/0008-phase-5-frontend.md.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    expires_at: datetime
