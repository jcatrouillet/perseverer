"""Settings loader: config/default.toml provides defaults, environment variables
(SPORTHEALTH_*) and .env override them. Env takes precedence over TOML so a container's
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
        env_prefix="SPORTHEALTH_",
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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sporthealth.db"

    @property
    def raw_archive_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def garmin_tokenstore_dir(self) -> Path:
        return self.data_dir / "garmin_tokens"

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
