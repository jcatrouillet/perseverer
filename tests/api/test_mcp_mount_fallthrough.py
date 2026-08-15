"""Regression test for a real incident: `app.mount("/", mcp_asgi_app)` (main.py) catches every
request FastAPI's own routers didn't match, not just ones meant for the MCP tool surface. Before
the fix in mcp_server.py's `_require_api_key_asgi`, any unmatched path -- including a genuinely
missing/misregistered API route -- got turned into a misleading "invalid API key" 401 instead of
a normal 404, which is indistinguishable from a real auth failure and caused the frontend's
"clear credentials on any 401" logic to silently log a JWT-authenticated user out.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_unmatched_non_mcp_path_returns_404_not_a_misleading_401(client: TestClient) -> None:
    r = client.get("/api/v1/this-route-does-not-exist")
    assert r.status_code == 404


def test_unmatched_path_404s_even_with_no_credential_at_all(client: TestClient) -> None:
    # No X-API-Key, no Authorization header -- still a plain 404, not the MCP wrapper's 401.
    r = client.get("/some/completely/unrelated/path")
    assert r.status_code == 404
