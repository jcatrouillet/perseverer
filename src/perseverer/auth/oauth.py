"""OAuth 2.1 authorization server for the MCP endpoint, so a remote MCP client that can't send a
custom header (claude.ai's "custom connector") can authenticate. See docs/adr/0007-phase-4-mcp-
server.md decision 10.

The MCP SDK supplies the protocol surface (`/.well-known/*`, `/authorize`, `/token`, `/register`,
`/revoke`, PKCE verification, redirect-URI checks); this module supplies the storage and the one
piece the SDK can't: *who is allowed in*. The resource owner authenticates on this app's own
login page (`api/routers/oauth.py`) with their existing Perseverer username/password, through the
same lockout-protected check `/auth/login` uses.

State lives in SQLite (`oauth_client`, `oauth_authorization_code`, `oauth_token`), not memory:
`api` runs two uvicorn workers that share no process memory. Codes and tokens are stored only as
SHA-256 hashes. The pending authorization request between `/authorize` and the login form is a
short-lived signed JWT carried in the URL, so it needs no table.

A raw `PERSEVERER_API_KEY` is also accepted as a bearer token (`load_access_token`): the ASGI
wrapper in `api/mcp_server.py` rewrites a valid `X-API-Key` header into one, which is how Claude
Code's existing header-based registration keeps working unchanged.

The MCP tools act as the default athlete (they call the REST layer with the shared API key), so
only that athlete may complete the login -- see `api/routers/oauth.py`.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import Connection, Engine, delete, select

from perseverer.config import get_settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import oauth_authorization_code, oauth_client, oauth_token
from perseverer.db.seed import DEFAULT_ATHLETE_ID

SCOPE = "mcp"
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
REQUEST_TTL = timedelta(minutes=10)
_ALGORITHM = "HS256"
_REQUEST_AUDIENCE = "perseverer-oauth-request"


def _now() -> datetime:
    """Naive UTC, matching every DateTime column in this schema (ADR 0002 decision 10)."""
    return datetime.now(UTC).replace(tzinfo=None)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _scopes(text: str) -> list[str]:
    return text.split()


def create_request_token(secret: str, payload: dict[str, Any]) -> str:
    """The pending /authorize request, signed so the login form can't be pointed at a redirect
    URI or PKCE challenge the client never registered."""
    claims = {**payload, "aud": _REQUEST_AUDIENCE, "exp": datetime.now(UTC) + REQUEST_TTL}
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)


def decode_request_token(secret: str, token: str) -> dict[str, Any] | None:
    try:
        decoded: dict[str, Any] = jwt.decode(
            token, secret, algorithms=[_ALGORITHM], audience=_REQUEST_AUDIENCE
        )
        return decoded
    except jwt.PyJWTError:
        return None


def issue_authorization_code(
    conn: Connection,
    *,
    athlete_id: str,
    client_id: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    code_challenge: str,
    scopes: list[str],
    resource: str | None,
) -> str:
    """Stores a single-use authorization code and returns the raw value (never recoverable
    afterwards). Opportunistically prunes expired codes, keeping the table bounded without a
    scheduled job."""
    now = _now()
    conn.execute(
        delete(oauth_authorization_code).where(oauth_authorization_code.c.expires_at < now)
    )
    code = secrets.token_urlsafe(32)
    conn.execute(
        oauth_authorization_code.insert().values(
            code_hash=hash_token(code),
            athlete_id=athlete_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            code_challenge=code_challenge,
            scopes=" ".join(scopes),
            resource=resource,
            expires_at=now + CODE_TTL,
        )
    )
    conn.commit()
    return code


class PersevererOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, engine_factory: Callable[[], Engine] | None = None) -> None:
        self._engine_factory = engine_factory
        self._engine: Engine | None = None

    def _get_engine(self) -> Engine:
        if self._engine_factory is not None:
            return self._engine_factory()
        if self._engine is None:
            self._engine = make_engine(get_settings().db_path)
        return self._engine

    # --- clients (RFC 7591 dynamic registration) ---------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._get_engine().connect() as conn:
            row = conn.execute(
                select(oauth_client.c.client_info_json).where(oauth_client.c.client_id == client_id)
            ).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row.client_info_json)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is assigned by the registration handler")
        with self._get_engine().connect() as conn:
            conn.execute(
                oauth_client.insert().values(
                    client_id=client_info.client_id,
                    client_info_json=client_info.model_dump_json(),
                    created_at=_now(),
                )
            )
            conn.commit()

    # --- authorization code flow -------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        secret = get_settings().jwt_secret
        if not secret:
            raise AuthorizeError(
                error="server_error", error_description="JWT signing is not configured"
            )
        token = create_request_token(
            secret,
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "state": params.state,
                "scopes": params.scopes or [SCOPE],
                "resource": params.resource,
            },
        )
        # Relative on purpose: the browser resolves it against whatever origin it actually used to
        # reach this server, so no public-hostname setting is needed for it to be right.
        return f"/oauth/login?req={token}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with self._get_engine().connect() as conn:
            row = conn.execute(
                select(oauth_authorization_code).where(
                    oauth_authorization_code.c.code_hash == hash_token(authorization_code)
                )
            ).fetchone()
        if row is None or row.client_id != client.client_id or row.expires_at < _now():
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=_scopes(row.scopes),
            expires_at=row.expires_at.replace(tzinfo=UTC).timestamp(),
            client_id=row.client_id,
            code_challenge=row.code_challenge,
            redirect_uri=row.redirect_uri,
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
            resource=row.resource,
            subject=row.athlete_id,
        )

    def _issue_pair(
        self,
        conn: Connection,
        *,
        athlete_id: str,
        client_id: str,
        scopes: list[str],
        resource: str | None,
    ) -> OAuthToken:
        now = _now()
        conn.execute(delete(oauth_token).where(oauth_token.c.expires_at < now))
        grant_id = secrets.token_urlsafe(16)
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        for kind, value, ttl in (("access", access, ACCESS_TTL), ("refresh", refresh, REFRESH_TTL)):
            conn.execute(
                oauth_token.insert().values(
                    token_hash=hash_token(value),
                    athlete_id=athlete_id,
                    client_id=client_id,
                    kind=kind,
                    scopes=" ".join(scopes),
                    resource=resource,
                    grant_id=grant_id,
                    expires_at=now + ttl,
                    created_at=now,
                )
            )
        conn.commit()
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=int(ACCESS_TTL.total_seconds()),
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with self._get_engine().connect() as conn:
            # Deleting is the single-use guarantee: a second exchange of the same code finds
            # nothing to delete and is refused, even if it raced the first one.
            deleted = conn.execute(
                delete(oauth_authorization_code).where(
                    oauth_authorization_code.c.code_hash == hash_token(authorization_code.code)
                )
            ).rowcount
            if deleted != 1 or authorization_code.subject is None:
                conn.rollback()
                raise TokenError(
                    error="invalid_grant", error_description="authorization code already used"
                )
            return self._issue_pair(
                conn,
                athlete_id=authorization_code.subject,
                client_id=authorization_code.client_id,
                scopes=authorization_code.scopes,
                resource=authorization_code.resource,
            )

    # --- refresh -----------------------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = self._load_token_row(refresh_token, "refresh")
        if row is None or row.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row.client_id,
            scopes=_scopes(row.scopes),
            expires_at=int(row.expires_at.replace(tzinfo=UTC).timestamp()),
            subject=row.athlete_id,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        granted = set(refresh_token.scopes)
        if not set(scopes) <= granted:
            raise TokenError(error="invalid_scope", error_description="scope was not granted")
        with self._get_engine().connect() as conn:
            # Rotation: the old pair is revoked as the new one is issued, so a stolen refresh
            # token stops working the moment the legitimate client next refreshes.
            row = conn.execute(
                select(oauth_token.c.grant_id, oauth_token.c.resource).where(
                    oauth_token.c.token_hash == hash_token(refresh_token.token)
                )
            ).fetchone()
            if row is None or refresh_token.subject is None:
                raise TokenError(error="invalid_grant", error_description="refresh token revoked")
            conn.execute(delete(oauth_token).where(oauth_token.c.grant_id == row.grant_id))
            return self._issue_pair(
                conn,
                athlete_id=refresh_token.subject,
                client_id=refresh_token.client_id,
                scopes=scopes or refresh_token.scopes,
                resource=row.resource,
            )

    # --- access tokens -----------------------------------------------------------------------

    def _load_token_row(self, token: str, kind: str) -> Any:
        with self._get_engine().connect() as conn:
            row = conn.execute(
                select(oauth_token).where(
                    oauth_token.c.token_hash == hash_token(token), oauth_token.c.kind == kind
                )
            ).fetchone()
        if row is None or row.expires_at < _now():
            return None
        return row

    async def load_access_token(self, token: str) -> AccessToken | None:
        api_key = get_settings().api_key
        if api_key and secrets.compare_digest(token, api_key):
            return AccessToken(
                token=token,
                client_id="api-key",
                scopes=[SCOPE],
                expires_at=None,
                subject=DEFAULT_ATHLETE_ID,
            )
        row = self._load_token_row(token, "access")
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row.client_id,
            scopes=_scopes(row.scopes),
            expires_at=int(row.expires_at.replace(tzinfo=UTC).timestamp()),
            resource=row.resource,
            subject=row.athlete_id,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self._get_engine().connect() as conn:
            row = conn.execute(
                select(oauth_token.c.grant_id).where(
                    oauth_token.c.token_hash == hash_token(token.token)
                )
            ).fetchone()
            if row is not None:
                conn.execute(delete(oauth_token).where(oauth_token.c.grant_id == row.grant_id))
                conn.commit()


oauth_provider = PersevererOAuthProvider()

__all__ = [
    "ACCESS_TTL",
    "CODE_TTL",
    "REFRESH_TTL",
    "SCOPE",
    "PersevererOAuthProvider",
    "create_request_token",
    "decode_request_token",
    "hash_token",
    "issue_authorization_code",
    "oauth_provider",
]
