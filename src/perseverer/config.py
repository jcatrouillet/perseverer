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

    # --- Eufy Life body-composition sync (adapters/eufy.py) ---
    # All optional and unset by default -- sync_eufy() skips with a log line, not an error, when
    # unconfigured. Deliberately plain env-var credentials, not a token-store-only model like
    # garmin_connect's: no evidence Eufy's API shares Garmin's SSO lockout fragility, and this is
    # exactly how the sibling eufy-health-sync project already runs safely, daily, unattended.
    eufy_email: str | None = None
    eufy_password: str | None = None
    eufy_device_id: str | None = None
    eufy_customer_id: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "perseverer.db"

    @property
    def raw_archive_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def garmin_tokenstore_dir(self) -> Path:
        return self.data_dir / "garmin_tokens"

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
