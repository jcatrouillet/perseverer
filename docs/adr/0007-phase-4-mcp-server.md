# ADR 0007: Phase 4 — MCP server exposing the read API + notes

## Status

Accepted. Core mounting/lifespan/security mechanics verified by direct introspection and a
live `TestClient` round-trip against the installed `mcp` package before writing any production
code, per CLAUDE.md's standing instruction for fast-moving vendor libraries.

## Context

CLAUDE.md's own mission statement is "a REST/JSON API an AI agent can write notes through."
Phase 3 built that API; Phase 4 makes an AI agent's access to it a first-class MCP (Model
Context Protocol) tool surface instead of raw HTTP a human has to proxy. ADR 0001 (Phase 0)
speculated this "most likely gets its own Dockerfile" — a guess made before Streamable HTTP's
actual shape, or the Phase 3 API's actual shape, existed. Revisited below now that both do.

## Decisions

### 1. Package: `mcp` 1.x, not 2.x

`mcp` 2.0 went stable days before this phase started — a large breaking change (`FastMCP`
renamed to `MCPServer`, sync tool handlers now thread-dispatched, HTTP client swapped, resource
URI-template matching rewritten). The project's own migration guide states 1.x remains the
recommended choice for critical workloads. Pinned `mcp>=1.9,<2` (installed: 1.29.0). Revisit
the 2.x migration once it's had time to stabilize, not on day one of a brand-new major version.

### 2. Mounted into the existing `api` app, not a separate container

Streamable HTTP (the current, stable, recommended transport for a remotely-reachable server)
produces a plain ASGI app (`FastMCP.streamable_http_app()`) — confirmed directly, not assumed.
Mounting it at `/mcp` inside the existing FastAPI `app` (`api/main.py`) avoids a fourth
container on an 8GB-RAM box already budgeted at ~1.5GB total, and lets every MCP tool reuse the
REST layer's already-tested auth/validation/rollup/response-shaping logic instead of
reimplementing any of it. This deliberately overrides ADR 0001's Phase-0 guess now that the
real shape of both the API and the SDK's transport model are known.

### 3. Lifespan composition is manual, not automatic — confirmed by direct testing

`app.mount("/", mcp.streamable_http_app())` alone does **not** work: Starlette does not
propagate ASGI lifespan events into a mounted sub-application's own `lifespan=` parameter.
Verified directly: a raw mount raises `RuntimeError: Task group is not initialized. Make sure
to use run()` on the first request, because `StreamableHTTPSessionManager.run()` was never
entered. The fix, confirmed working end-to-end via `TestClient`: call
`mcp.streamable_http_app()` once up front (which lazily creates the session manager as a side
effect), then give the **parent** FastAPI app its own `lifespan=` that enters
`mcp.session_manager.run()` — `FastMCP.session_manager`'s own docstring says this property
exists exactly "to enable advanced use cases like mounting... FastMCP servers in a... FastAPI
application," confirming this is the SDK's intended pattern, not a workaround.

### 4. DNS-rebinding / Host-header protection disabled, with the API key as the real gate

The SDK's `TransportSecuritySettings` validates the request's `Host` header by default
(`enable_dns_rebinding_protection=True`), rejecting anything not on an explicit allowlist —
confirmed directly: a plain `TestClient` request 421'd on `Host: testserver` until this was
addressed. This protection exists to stop a malicious webpage from using DNS rebinding to talk
to a *locally-bound, otherwise-unauthenticated* service as if it were same-origin. It doesn't
fit this deployment: `/mcp` sits behind the same shared `X-API-Key` requirement as every other
data route (see decision 5) — a request-content-based credential check that DNS rebinding does
nothing to defeat, unlike Host-header trust. Configuring a real `allowed_hosts` list would also
require baking a deployment-specific hostname into application config for a protection this
threat model doesn't need. `enable_dns_rebinding_protection=False`, with the API key doing the
actual access-control work.

### 5. Auth: a small ASGI wrapper, since `Mount` bypasses FastAPI's `Depends`

Every other data route uses `Depends(require_api_key)` (ADR 0006 decision 6), but requests to a
`Mount`-ed sub-application never enter FastAPI's own routing/dependency-injection system —
`Depends` simply doesn't apply there. `/mcp` is instead wrapped in a small raw ASGI callable
(`_require_api_key_asgi`, `api/mcp_server.py`) checking `X-API-Key` against
`settings.api_key` (same `secrets.compare_digest`, same fail-closed-when-unset behavior as
`require_api_key`) before the request ever reaches the MCP session manager. One shared secret
still gates the whole service regardless of entry path — REST or MCP.

### 6. Tools call their REST endpoint in-process via ASGI transport, not direct DB access

Each `@mcp.tool()` function makes an in-process HTTP call to the same `app` object via
`httpx.AsyncClient(transport=httpx.ASGITransport(app=app))`, using the server's own configured
API key — the tool acts as the service's own trusted client. This reuses 100% of the REST
layer's pagination, rollup-backed calendar logic, and pydantic response shaping instead of a
second implementation against `Connection`/`duckdb` directly, and means
`tests/api/conftest.py`'s existing `dependency_overrides`-based fixtures work unchanged for MCP
tool tests (`app.dependency_overrides` apply regardless of which client reaches `app`).
`mcp_server.py` imports `app` from `api.main` lazily, inside each tool function — the same
pattern already used to break the real `fit_folder.py`/`ingest_dispatch.py` circular import in
Phase 2, applied here because `api/main.py` needs to mount the MCP app that needs `app` itself.

### 7. `get_activity_stream`'s tier is forced to `low`

Bulk per-second stream data doesn't belong in an agent's context window regardless of what a
tool caller requests — the tool ignores any tier argument and always requests `low` (~200
points) from the underlying `/activities/{id}/stream` endpoint.

## Consequences

- A future second MCP-capable service (unlikely, but ADR 0001's "second Python service" framing
  could apply) should follow the same mount-not-separate-container reasoning unless there's a
  concrete reason (isolation, independent scaling) this project doesn't currently have.
- The `mcp` 1.x → 2.x migration is now real, tracked debt, not hypothetical — revisit once 2.x
  has had a few months to stabilize and this project has a reason to need something it adds.
- Disabling DNS-rebinding protection (decision 4) is safe *because* decision 5's API-key gate
  exists — if the API-key requirement were ever removed or made optional, this decision would
  need revisiting alongside it, not independently.
