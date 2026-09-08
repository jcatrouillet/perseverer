"""Settings loader: config/default.toml provides defaults, environment variables
(PERSEVERER_*) and .env override them. Env takes precedence over TOML so a container's
runtime environment always wins over the baked-in default file.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

_DEFAULT_TOML = Path(__file__).resolve().parents[2] / "config" / "default.toml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=_DEFAULT_TOML,
        env_prefix="PERSEVERER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    data_dir: Path = Path("/data")

    # --- Phase 2: garmin_connect rate limiting, rolling window, staleness, scheduler ---
    garmin_rolling_window_days: int = 10
    garmin_request_interval_s: float = 3.0
    garmin_max_requests_per_hour: int = 300
    garmin_stale_escalate_days: int = 7
    export_freshness_days: int = 90
    staleness_webhook_url: str | None = None
    schedule_hour: int = 4
    schedule_minute: int = 15
    schedule_jitter_s: int = 600
    # IANA name (e.g. "America/Los_Angeles"), not a fixed UTC offset -- CronTrigger resolves
    # hour/minute against this zone's own wall clock every run, so the schedule stays correct
    # across DST transitions instead of drifting by an hour twice a year the way a hardcoded UTC
    # offset would. Defaults to UTC (APScheduler's own default), matching this setting's original
    # unconfigurable behavior for anyone not overriding it -- worker/main.py's own docstring
    # calling this "04:15 local" was previously inaccurate on any host not itself running in the
    # athlete's own timezone (bercy's system clock is UTC, not Pacific).
    schedule_timezone: str = "UTC"

    # --- Phase 3: read API auth/CORS/DuckDB ---
    # Shared secret for the X-API-Key header (api/dependencies.py::require_api_key). Unset ->
    # every protected route fails closed (503), never fails open. See ADR 0006 decision 6.
    api_key: str | None = None
    # Comma-separated origins for CORSMiddleware; unset -> no CORS middleware at all (no
    # cross-origin browser access by default). See ADR 0006 decision 8.
    cors_allowed_origins: str | None = None
    # Reserved, not yet wired to uvicorn's --forwarded-allow-ips (needs an api.Dockerfile CMD
    # change unrelated to Phase 3's core scope). See ADR 0006 decision 8 / decision 3 in the
    # deferred-scope section.
    trusted_proxy_ip: str | None = None
    # Where the DuckDB sqlite extension is baked in at Docker build time (api.Dockerfile) --
    # None locally, where DuckDB's own default cache/INSTALL is fine. See ADR 0006 decision 4.
    duckdb_extension_dir: Path | None = None

    # --- Phase 5: per-athlete login (auth/tokens.py, api/routers/auth.py) ---
    # Signs session JWTs. Unset -> /auth/login fails closed (503), mirroring api_key's
    # unset-503 precedent. See ADR 0008.
    jwt_secret: str | None = None
    jwt_expiry_days: int = 30

    # --- Share links (sharing.py, api/routers/share.py) ---
    # The externally-visible origin a share URL should point at (e.g.
    # "https://perseverer.example.com") -- needed because the request the create-share
    # endpoint handles arrives from *inside* the reverse proxy, whose own Host header doesn't
    # necessarily match what a link recipient's browser should actually open. Unset -> falls
    # back to the incoming request's own base URL (fine for local dev; production should set
    # this explicitly, same "don't guess the public hostname" reasoning as
    # PERSEVERER_API_BASE_URL on the frontend side, see docs/DEPLOY.md).
    public_base_url: str | None = None
    # Same CARTO basemap key the frontend already reads via PERSEVERER_CARTO_API_KEY
    # (mapBasemap.ts) -- not actually secret (CARTO's basemap product is designed for
    # client-side embedding, like a Mapbox public token), so embedding it in a public share
    # page's own MapLibre map is the same exposure the authenticated frontend already has.
    # Read here too because sharing.py's route map is rendered server-side by this process, not
    # the frontend container -- unset -> the CARTO style URL omits `?key=` (same graceful
    # degradation cartoStyleUrl() already has).
    carto_api_key: str | None = None

    # --- Eufy Life body-composition sync (adapters/eufy.py) ---
    # All optional and unset by default -- sync_eufy() skips with a log line, not an error, when
    # unconfigured. Deliberately plain env-var credentials, not a token-store-only model like
    # garmin_connect's: no evidence Eufy's API shares Garmin's SSO lockout fragility, and this is
    # exactly how the sibling eufy-health-sync project already runs safely, daily, unattended.
    eufy_email: str | None = None
    eufy_password: str | None = None
    eufy_device_id: str | None = None
    eufy_customer_id: str | None = None

    # --- Backup + restore automation (Phase 9, backup.py) ---
    # All optional and unset by default -- create_backup() logs and skips (not an error) when
    # unconfigured, same graceful-degradation contract as the Eufy block above. rsync over SSH to
    # a second host, not a cloud target: this is a home-lab single-NUC deployment (bercy), and
    # the user already has a second LAN host to rsync to. The one-time SSH key exchange
    # (`ssh-copy-id`) is a manual runbook step in docs/DEPLOY.md -- this app has no way to
    # provision credentials on a host it doesn't control.
    backup_host: str | None = None
    backup_user: str | None = None
    backup_path: str | None = None
    # Defaults to the invoking user's own default key (~/.ssh/id_ed25519 etc.) when unset --
    # only needed if the backup step must use a specific, dedicated key.
    backup_ssh_key_path: str | None = None
    backup_schedule_hour: int = 3
    backup_schedule_minute: int = 30
    # How many local timestamped DB snapshots to keep under <data_dir>/backups/ before pruning --
    # the remote rsync copy is the real backup; local snapshots only exist to be rsync'd from, so
    # there's no reason to let them accumulate forever.
    backup_keep_local_snapshots: int = 7

    # --- Scheduled workouts: automatic Garmin push (worker/main.py::run_daily_workout_push) ---
    # A planned workout gets automatically pushed once its local_date is within this many days --
    # the user's own choice ("push it if it's within the coming week"), not a full-calendar push
    # (pushing a workout scheduled months out would just clutter the watch's own workout list
    # long before the athlete cares). "Push now" (POST /planned-workouts/{workout_id}/push)
    # bypasses this window entirely for a manual override.
    planned_workout_push_window_days: int = 7
    # Its own hour/minute, right after the daily Garmin sync (default 04:15) -- late enough that
    # any workout scheduled *today* by the athlete overnight is still caught by the same
    # rolling-window check on tomorrow's run if this run's own DB read happened to race it.
    workout_push_schedule_hour: int = 4
    workout_push_schedule_minute: int = 45

    @property
    def db_path(self) -> Path:
        return self.data_dir / "perseverer.db"

    @property
    def raw_archive_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    def garmin_tokenstore_dir_for(self, athlete_id: str) -> Path:
        """Each athlete gets their own Garmin token-store directory -- before this, every athlete
        shared one global directory and whoever last ran `sync auth login` silently overwrote
        everyone else's session. See docs/DEPLOY.md for the one-time manual move of the original
        single-athlete deployment's existing token directory into its own namespaced subpath."""
        return self.data_dir / "garmin_tokens" / athlete_id

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def backup_known_hosts_path(self) -> Path:
        # A dedicated known_hosts file under /data (always writable), not backup.py's own
        # read-only-mounted SSH key directory -- see backup.py::_rsync's own docstring for why
        # accept-new's first-contact write needs a genuinely writable path once the worker
        # container's root filesystem is read-only (Phase 9 container hardening, ADR 0014).
        return self.data_dir / "backup_known_hosts"

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.cors_allowed_origins:
            return []
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority, highest first: explicit init kwargs, env vars, .env file, TOML defaults.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
