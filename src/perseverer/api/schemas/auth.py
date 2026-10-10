"""Request/response models for POST /auth/login and the password reset endpoints. See
docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    expires_at: datetime


class ForgotPasswordRequest(BaseModel):
    # The account's username or email address.
    identifier: str = Field(min_length=1, max_length=320)


class ForgotPasswordResponse(BaseModel):
    # Whether this server can send email at all. Says nothing about the account: the response is
    # the same whether or not it exists or has an email address.
    email_configured: bool


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class ResetPasswordResponse(BaseModel):
    success: bool
