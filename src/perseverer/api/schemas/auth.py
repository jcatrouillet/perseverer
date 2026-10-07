"""Request/response models for POST /auth/login. See
docs/ARCHITECTURE.md.
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
