"""Request/response models for the share-link management endpoints (api/routers/share.py). The
public `GET /share/{token}` endpoint returns server-rendered HTML directly, not JSON -- see
sharing.py -- so it has no schema here.
"""

from __future__ import annotations

from pydantic import BaseModel


class ShareLinkOut(BaseModel):
    id: int
    url: str


class RevokeShareOut(BaseModel):
    revoked: bool
