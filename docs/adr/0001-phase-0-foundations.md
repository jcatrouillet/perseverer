# ADR 0001: Phase 0 foundations — packaging, container shape, config, and CI

## Status

Accepted. Amended 2026-08-02: dev container engine changed from Docker Desktop to Podman
Desktop — see decision 8.

## Context

Phase 0 delivers the repo skeleton the rest of the project builds on: nothing here is
application logic (no adapters, no schema, no real frontend), but the choices made now — how
Python is packaged, how the API/worker containers relate, how config is layered, how CI
catches the DS1019+'s missing-AVX problem — are expensive to change later. This ADR records
those choices and why, per the project's requirement that every new dependency and structural
decision get a one-line justification.

Confirmed constraints going in:
- DSM's native Python is 3.9, but everything runs in Docker containers with their own
  runtime, so it doesn't constrain the container's Python version. Node on the homelab is v22.
- Git hosting: GitHub, private repo, GitHub Actions for CI.
- Image delivery to the NAS: GHCR, pulled by tag from DSM Container Manager.
- Target CPU (DS1019+, Celeron J3455 / Goldmont) has no AVX/AVX2 — only up to SSE4.2.

## Decisions

### 1. `uv` for Python packaging, single `perseverer` package, src-layout

One package shared by the `api` and `worker` containers (two thin entrypoints —
`perseverer.api.main:app` and `perseverer.worker.main`) rather than two separate
distributions, because they will share the schema, adapter interfaces, and config code
starting Phase 1-2. `uv` because it's a single fast tool for venv + dependency resolution +
lockfile, and multi-stage Docker builds shell out to it cleanly (`uv sync --frozen`).

### 2. One Dockerfile per role (`api.Dockerfile`, `worker.Dockerfile`), not one image + compose `command:` override

Each gets its own minimal runtime image, independently tagged and pullable on the NAS. The
alternative (one image, `command:` swapped per service in compose) was rejected because it
means the worker image always carries whatever the API needs (and vice versa) even if the
dependency sets diverge later (e.g. FIT-parsing-heavy libs the API never touches).

### 3. Config: `pydantic-settings`, TOML defaults + env override

`config/default.toml` holds non-secret defaults; environment variables (`PERSEVERER_*`) and
`.env` override them, in that priority order (env wins). This is the standard 12-factor shape
and keeps secrets out of any committed file. Flat keys for now (no nested TOML tables) — there
isn't enough config surface yet to justify the nesting; revisit once Phase 2's
adapter/scheduler config lands.

### 4. Three compose files: base + dev override (auto-merged) + NAS override (explicit `-f`)

`compose.yaml` has no bind mounts and publishes nothing. `compose.override.yml` is picked up
automatically by a plain `compose up` invocation on Windows (`docker compose up` originally;
`podman compose up` since decision 8 — both auto-merge override files identically, since this
is a Compose Specification behavior, not a Docker-specific one) — this is what makes the
Phase 0 acceptance criterion (a healthy `/healthz` with no extra flags) trivially true.
`compose.nas.yml` is applied explicitly (`-f compose.yaml -f compose.nas.yml`) and adds the
`user:` UID/GID mapping, the NAS host bind mount, and per-service memory/CPU caps — never
ports, since the DSM reverse proxy is the sole ingress from Phase 3 onward.

Rejected: a single compose file with profiles. Three files map directly to "how do I run this
right now" (plain `up` = dev, explicit NAS flags = prod) without needing to remember a profile
name, and the asymmetry (dev publishes ports, NAS never does) is exactly the kind of thing that
should be structurally impossible to get backwards, not opt-in.

### 5. AVX-masking verification strategy: QEMU `Westmere` CPU model in CI

GitHub Actions runners are AVX2-capable, so we can't rely on real hardware to catch an
x86-64-v3-compiled dependency. `tests/test_avx_smoke.py` (exercising numpy/pyarrow/duckdb) runs
both natively (`test` job) and under `qemu-x86_64 -cpu <model>` (`avx-smoke` job) — an AVX-only
code path SIGILLs under emulation instead of silently passing on the runner and then crashing on
the NAS at 4am.

**Resolved** (previously flagged as unvalidated): the first choice, `-cpu qemu64`, SIGILL'd on
every single CI run once actually exercised — but not because of AVX/AVX2. QEMU's `qemu64`
model is a generic baseline that, by default, doesn't even include SSE4.1/SSE4.2 — a *stricter*
and non-representative floor than the real DS1019+ CPU, which has SSE4.2. numpy's own wheel
crashes on that missing SSE4.x baseline before the AVX/AVX2 question this gate exists to answer
is ever reached — a false alarm, confirmed by reproducing both outcomes directly (`qemu-x86_64
-cpu qemu64 python3 -c "import numpy"` SIGILLs; the identical command with `-cpu Westmere`
succeeds). Switched to `-cpu Westmere` (SSE4.2 + AES-NI, no AVX — both features Goldmont also
has), which correctly represents the real target and lets the gate test what it's meant to:
AVX/AVX2-only code paths, not an artificially narrower baseline. numpy/pyarrow/duckdb were
added as dependencies in Phase 0 (unused until Phase 1+) specifically so this gate has
something real to exercise from day one.

### 6. `pyarrow` mypy override

`pyarrow` ships no `py.typed` marker as of this writing, so `mypy --strict` needs an explicit
`ignore_missing_imports` override for `pyarrow.*` rather than failing the whole build on a
third-party gap.

### 7. Frontend dependency versions

`vite`, `@vitejs/plugin-react`, and `typescript` were pinned to their current latest majors at
setup time (not the versions originally sketched) after `npm install` flagged the previous
`vite` line on a moderate-severity dev-server advisory (GHSA-67mh-4wv8-2f99, dev-server-only,
not a production build issue). Since this repo is brand new, there's no reason to start on a
version with a known advisory when the current major resolves cleanly.

### 8. Dev container engine: Podman Desktop instead of Docker Desktop

Local dev on Windows uses Podman Desktop rather than Docker Desktop. No file in this repo
needed to change for this: `compose.yaml`/`compose.override.yml`/`compose.nas.yml` are plain
Compose Specification with no Docker-only extensions (no BuildKit-only Dockerfile features
either — both Dockerfiles are standard multi-stage `FROM`/`COPY --from=`/`RUN`/`USER`/`CMD`,
which `podman build` handles natively via buildah). The dev commands change from
`docker compose ...` to `podman compose ...`; everything else in this ADR stands unchanged.

This is dev-only. **The NAS is unaffected** — DSM Container Manager runs Docker, not Podman,
so `compose.nas.yml` and the GHCR pull-based deploy flow in `docs/DEPLOY.md` stay exactly as
designed. Podman's rootless-by-default model also happens to line up with the non-root `USER
perseverer` already set in both `api.Dockerfile` and `worker.Dockerfile` — no change needed
there either.

**Verified, after real debugging.** Once Podman Desktop was installed, `podman compose up
--build` plus `curl http://localhost:8008/api/v1/healthz` and the frontend on `:5173` both
came back 200 — but getting there surfaced three real, worth-recording issues, two in this
repo and one in the default Podman Desktop setup:

1. **venv path mismatch (repo bug, fixed)**: both Dockerfiles built the uv venv at `/build` in
   the builder stage and copied it to `/app` in the runtime stage. uv bakes the venv's
   absolute path into `.venv/bin/*` script shebangs, so `uvicorn` couldn't find its own
   interpreter at runtime (`exec ... uvicorn: No such file or directory`) — the api container
   crash-looped. Fixed by building at `/app` in both stages so the path matches.
2. **Image name collision (repo bug, fixed)**: `compose.yaml` used
   `${GHCR_IMAGE_PREFIX:-perseverer-api}:${IMAGE_TAG:-local}`. That fallback only applies when
   the variable is *unset* — since `.env` sets `GHCR_IMAGE_PREFIX` (for the NAS), all three
   services silently resolved to the identical image name. Fixed by decoupling local dev tags
   entirely from the GHCR prefix: `compose.yaml` now hardcodes `perseverer-{api,worker,frontend}:local`,
   and only `compose.nas.yml` references `GHCR_IMAGE_PREFIX`/`IMAGE_TAG`.
3. **Podman machine needs User-Mode Networking (environment, not a repo bug)**: the default
   Podman machine Podman Desktop creates on Windows (`UserModeNetworking: false`) never
   forwards published container ports to Windows `localhost`, regardless of rootful/rootless.
   Root-caused by testing both modes directly: rootful containers publish ports via iptables
   NAT, which WSL2's automatic localhost-forwarding doesn't detect (it looks for real listening
   sockets); rootless containers do have a real listening socket, but it lives inside a private
   per-user network namespace that isn't reachable even from the VM's own root context, let
   alone from Windows. Recreating the machine with `podman machine init --user-mode-networking`
   routes container ports through Podman's `gvproxy`, which sits at the right layer to solve
   this — and then it just worked. (One more wrinkle on the way: the recreated machine's first
   boot hit a WSL2 cgroup race — `user@1000.service` failed with "Device or resource busy" —
   cleared by `podman machine stop` + `wsl --shutdown` + `podman machine start`.) This is now
   documented as a required one-time setup step in `docs/DEPLOY.md`, since it's silent and easy
   to hit again on any fresh Podman Desktop install.

## Consequences

- Adding a second Python service later (e.g. a dedicated MCP server process in Phase 4) most
  likely gets its own `Dockerfile` following the same pattern, not folded into `api` or
  `worker`.
- The `compose.override.yml` / `compose.nas.yml` split means "which environment am I looking
  at" is always answerable from the command line invocation alone — no hidden env-var-driven
  branching inside a single compose file.
- The AVX-masked CI gate did pan out, once pointed at the right QEMU CPU model (decision 5) —
  it went from SIGILL-ing on every push (a false alarm from an overly-strict, non-representative
  baseline) to green with no code changes needed elsewhere, confirming numpy/pyarrow/duckdb's
  wheels genuinely do runtime-dispatch cleanly on an SSE4.2-no-AVX target. First deploy to the
  DS1019+ is still the ultimate real-hardware confirmation, but this is no longer "flying blind"
  until then.
