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

### 1. `uv` for Python packaging, single `sporthealth` package, src-layout

One package shared by the `api` and `worker` containers (two thin entrypoints —
`sporthealth.api.main:app` and `sporthealth.worker.main`) rather than two separate
distributions, because they will share the schema, adapter interfaces, and config code
starting Phase 1-2. `uv` because it's a single fast tool for venv + dependency resolution +
lockfile, and multi-stage Docker builds shell out to it cleanly (`uv sync --frozen`).

### 2. One Dockerfile per role (`api.Dockerfile`, `worker.Dockerfile`), not one image + compose `command:` override

Each gets its own minimal runtime image, independently tagged and pullable on the NAS. The
alternative (one image, `command:` swapped per service in compose) was rejected because it
means the worker image always carries whatever the API needs (and vice versa) even if the
dependency sets diverge later (e.g. FIT-parsing-heavy libs the API never touches).

### 3. Config: `pydantic-settings`, TOML defaults + env override

`config/default.toml` holds non-secret defaults; environment variables (`SPORTHEALTH_*`) and
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

### 5. AVX-masking verification strategy: QEMU `qemu64` CPU model in CI

GitHub Actions runners are AVX2-capable, so we can't rely on real hardware to catch an
x86-64-v3-compiled dependency. The plan is to run the same pytest smoke test
(`tests/test_avx_smoke.py`, exercising numpy/pyarrow/duckdb) both natively (`test` job) and
under `qemu-x86_64 -cpu qemu64` (`avx-smoke` job) — the `qemu64` model has no AVX/AVX2, so an
AVX-only code path SIGILLs under emulation instead of silently passing on the runner and then
crashing on the NAS at 4am.

**Flagged uncertainty**: this is the one piece of this ADR I haven't validated end-to-end in
GitHub Actions yet. If `qemu-user-static` proves unreliable there, the fallback is manual wheel
tag vetting (numpy/pyarrow/duckdb already ship `manylinux` wheels with runtime CPU dispatch)
plus a one-time manual check on first NAS deploy, with the CI gate best-effort rather than a
hard requirement. numpy/pyarrow/duckdb were added as dependencies in Phase 0 (unused until
Phase 1+) specifically so this gate has something real to exercise from day one.

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
sporthealth` already set in both `api.Dockerfile` and `worker.Dockerfile` — no change needed
there either.

**Not yet verified**: no container engine (Docker Desktop or Podman Desktop) is installed on
the machine this repo was scaffolded on, so the Phase 0 acceptance criterion (`compose up`
serving a healthy `/healthz`) is still unverified end-to-end as of this amendment. Everything
short of that — `uv run pytest`/`ruff`/`mypy`, the frontend `typecheck`/`build`, and YAML
validation of all three compose files — passes locally.

## Consequences

- Adding a second Python service later (e.g. a dedicated MCP server process in Phase 4) most
  likely gets its own `Dockerfile` following the same pattern, not folded into `api` or
  `worker`.
- The `compose.override.yml` / `compose.nas.yml` split means "which environment am I looking
  at" is always answerable from the command line invocation alone — no hidden env-var-driven
  branching inside a single compose file.
- If the AVX-masked CI gate doesn't pan out, first real validation of the AVX assumption moves
  to "first deploy to the DS1019+" (end of Phase 3 per the phase table) — acceptable since
  nothing ingests real data before then, but worth revisiting before Phase 1 backfill work
  begins in earnest.
