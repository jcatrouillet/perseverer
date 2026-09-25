"""The resource-owner login page for the MCP OAuth flow (auth/oauth.py, ADR 0007 decision 10).

The SDK's `/authorize` redirects the browser here with a signed `req` token describing the pending
authorization request; a correct Perseverer username/password mints a single-use authorization
code and sends the browser back to the client's own redirect URI. Public by omission (no
`require_api_key`) -- this page IS the credential check -- and it reuses `/auth/login`'s exact
lockout-protected check (`auth/credentials.py`).
"""

from __future__ import annotations

from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from mcp.server.auth.provider import construct_redirect_uri
from sqlalchemy import Connection

from perseverer.api.dependencies import SettingsDep, get_conn
from perseverer.auth.credentials import authenticate_athlete
from perseverer.auth.oauth import decode_request_token, issue_authorization_code
from perseverer.db.seed import DEFAULT_ATHLETE_ID

router = APIRouter()

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Perseverer &mdash; authorize access</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #f3f4f7; color: #1c2230; margin: 0;
         display: grid; place-items: center; min-height: 100vh; }}
  main {{ background: #fff; padding: 2rem; border-radius: 10px; max-width: 24rem; width: 90%;
         box-shadow: 0 2px 12px rgba(0,0,0,.08); }}
  h1 {{ font-size: 1.2rem; margin: 0 0 .5rem; }}
  p {{ font-size: .9rem; line-height: 1.4; }}
  label {{ display: block; font-size: .85rem; margin-top: 1rem; }}
  input {{ width: 100%; box-sizing: border-box; padding: .55rem; margin-top: .25rem;
          border: 1px solid #b8bfcc; border-radius: 6px; font-size: 1rem; }}
  button {{ margin-top: 1.25rem; width: 100%; padding: .65rem; border: 0; border-radius: 6px;
           background: #2563eb; color: #fff; font-size: 1rem; cursor: pointer; }}
  .err {{ color: #b91c1c; }}
</style></head>
<body><main>
<h1>Authorize access to Perseverer</h1>
<p><strong>{client}</strong> is asking to read your training data and make changes on your
behalf, including scheduling workouts. Sign in to allow it.</p>
{error}
<form method="post" action="/oauth/login">
<input type="hidden" name="req" value="{req}">
<label>Username<input name="username" autocomplete="username" required autofocus></label>
<label>Password<input name="password" type="password" autocomplete="current-password" required>
</label>
<button type="submit">Authorize</button>
</form>
</main></body></html>"""


def _page(req: str, client: str, error: str = "", status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(
            client=escape(client),
            req=escape(req, quote=True),
            error=f'<p class="err" role="alert">{escape(error)}</p>' if error else "",
        ),
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


@router.get("/oauth/login", response_class=HTMLResponse)
def login_page(settings: SettingsDep, req: Annotated[str, Query()]) -> Response:
    claims = decode_request_token(settings.jwt_secret or "", req) if settings.jwt_secret else None
    if claims is None:
        return HTMLResponse("This authorization request is invalid or has expired.", 400)
    return _page(req, str(claims.get("client_id", "An application")))


@router.post("/oauth/login")
def login_submit(
    settings: SettingsDep,
    req: Annotated[str, Form()],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    conn: Connection = Depends(get_conn),
) -> Response:
    claims = decode_request_token(settings.jwt_secret or "", req) if settings.jwt_secret else None
    if claims is None:
        return HTMLResponse("This authorization request is invalid or has expired.", 400)
    client_label = str(claims.get("client_id", "An application"))

    athlete_id = authenticate_athlete(conn, username, password)
    if athlete_id is None:
        return _page(req, client_label, "Invalid username or password.", 401)
    if athlete_id != DEFAULT_ATHLETE_ID:
        # The MCP tools call the REST layer with the deployment's shared API key, i.e. as the
        # default athlete -- letting a second athlete authorize would show them the first
        # athlete's data. Refuse rather than mislead.
        return _page(
            req,
            client_label,
            "The MCP connector is only available to the deployment's primary athlete.",
            403,
        )

    code = issue_authorization_code(
        conn,
        athlete_id=athlete_id,
        client_id=str(claims["client_id"]),
        redirect_uri=str(claims["redirect_uri"]),
        redirect_uri_provided_explicitly=bool(claims["redirect_uri_provided_explicitly"]),
        code_challenge=str(claims["code_challenge"]),
        scopes=list(claims["scopes"]),
        resource=claims.get("resource"),
    )
    return RedirectResponse(
        construct_redirect_uri(str(claims["redirect_uri"]), code=code, state=claims.get("state")),
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )
