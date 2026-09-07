"""API tests for the public GET /share/calendar/{token}.ics feed (no auth at all). See
tests/test_calendar_feed.py for the underlying pure functions, and tests/api/test_settings.py for
the authenticated GET/POST/DELETE /settings/calendar-feed management endpoints that mint tokens.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.calendar_feed import generate_feed_token, hash_feed_token
from perseverer.db.schema import athlete
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def _publish(engine: Engine, raw_token: str) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(calendar_feed_token_hash=hash_feed_token(raw_token))
        )
        conn.commit()


def test_valid_token_returns_calendar(client: TestClient, engine: Engine) -> None:
    raw_token = generate_feed_token()
    _publish(engine, raw_token)

    r = client.get(f"/share/calendar/{raw_token}.ics")  # deliberately no auth_headers
    assert r.status_code == 200
    assert "text/calendar" in r.headers["content-type"]
    assert r.text.startswith("BEGIN:VCALENDAR")
    assert "END:VCALENDAR" in r.text


def test_unknown_token_returns_404(client: TestClient) -> None:
    r = client.get("/share/calendar/not-a-real-token.ics")
    assert r.status_code == 404


def test_revoked_token_returns_404(client: TestClient, engine: Engine) -> None:
    raw_token = generate_feed_token()
    _publish(engine, raw_token)
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(calendar_feed_token_hash=None)
        )
        conn.commit()

    r = client.get(f"/share/calendar/{raw_token}.ics")
    assert r.status_code == 404
