"""Tests for auth/tokens.py -- JWT session tokens."""

import datetime as dt

import jwt
import pytest

from perseverer.auth.tokens import InvalidSessionToken, create_session_token, verify_session_token


def test_round_trip() -> None:
    token, expires_at = create_session_token("athlete1", "secret", expiry_days=30)
    assert verify_session_token(token, "secret") == "athlete1"
    assert expires_at > dt.datetime.now(dt.UTC)


def test_wrong_secret_rejected() -> None:
    token, _ = create_session_token("athlete1", "secret", expiry_days=30)
    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, "wrong-secret")


def test_expired_token_rejected() -> None:
    token, _ = create_session_token("athlete1", "secret", expiry_days=-1)
    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, "secret")


def test_token_missing_sub_claim_rejected() -> None:
    now = dt.datetime.now(dt.UTC)
    token = jwt.encode({"iat": now, "exp": now + dt.timedelta(days=1)}, "secret", algorithm="HS256")
    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, "secret")
