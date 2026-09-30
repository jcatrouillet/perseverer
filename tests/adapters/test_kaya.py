from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from perseverer.adapters import kaya


class _Resp:
    def __init__(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self._body = body or {}

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_login_stores_tokens_not_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(path: str, body: dict[str, Any], base_url: str) -> _Resp:
        calls.append((path, body))
        return _Resp(200, {"token": "t1", "refresh_token": "r1", "user": {"id": 42}})

    monkeypatch.setattr(kaya, "_post", fake_post)
    tokens = kaya.login_with_credentials("a@b.c", "hunter2", tmp_path)
    assert (tokens.token, tokens.refresh_token, tokens.user_id) == ("t1", "r1", "42")
    assert calls[0][0] == "/api/user/login"
    assert "hunter2" not in (tmp_path / kaya.TOKEN_FILENAME).read_text()
    assert kaya.token_status(tmp_path) == (True, 0)


def test_invalid_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kaya, "_post", lambda *a, **k: _Resp(401))
    with pytest.raises(kaya.KayaInvalidCredentials):
        kaya.login_with_credentials("a@b.c", "bad", tmp_path)
    assert kaya.token_status(tmp_path) == (False, None)


def test_refresh_keeps_refresh_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kaya.save_tokens(tmp_path, kaya.KayaTokens("old", "r1", "42", "2026-01-01T00:00:00+00:00"))
    monkeypatch.setattr(kaya, "_post", lambda *a, **k: _Resp(200, {"token": "new"}))
    refreshed = kaya.refresh_access_token(tmp_path)
    assert (refreshed.token, refreshed.refresh_token) == ("new", "r1")


def test_refresh_dead_session_requires_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kaya.save_tokens(tmp_path, kaya.KayaTokens("old", "r1", "42", "2026-01-01T00:00:00+00:00"))
    monkeypatch.setattr(kaya, "_post", lambda *a, **k: _Resp(401))
    with pytest.raises(kaya.KayaAuthRequired):
        kaya.refresh_access_token(tmp_path)


def test_no_session_requires_login(tmp_path: Path) -> None:
    with pytest.raises(kaya.KayaAuthRequired):
        kaya.load_tokens(tmp_path)
