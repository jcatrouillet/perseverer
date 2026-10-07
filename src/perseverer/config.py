"""Settings loader: every setting has a default here, overridden by `perseverer.env` (the one
configuration file, see perseverer.env.example) and then by real environment variables
(PERSEVERER_*), so a container's runtime environment always wins.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PERSEVERER_",
        env_file="perseverer.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"
    # ./data on the dev host; the container images set PERSEVERER_DATA_DIR=/data themselves.
    data_dir: Path = Path("data")

    # --- garmin_connect rate limiting, rolling window, staleness, scheduler ---
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
    # athlete's own timezone (server clocks usually run on UTC).
    schedule_timezone: str = "UTC"

    # --- read API auth/CORS/DuckDB ---
    # Shared secret for the X-API-Key header (api/dependencies.py::require_api_key). Unset ->
    # every protected route fails closed (503), never fails open. see docs/ARCHITECTURE.md.
    api_key: str | None = None
    # Comma-separated origins for CORSMiddleware; unset -> no CORS middleware at all (no
    # cross-origin browser access by default). see docs/ARCHITECTURE.md.
    cors_allowed_origins: str | None = None
    # Where the DuckDB sqlite extension is baked in at Docker build time (api.Dockerfile) --
    # None locally, where DuckDB's own default cache/INSTALL is fine. see docs/ARCHITECTURE.md.
    duckdb_extension_dir: Path | None = None

    # --- per-athlete login (auth/tokens.py, api/routers/auth.py) ---
    # Signs session JWTs. Unset -> /auth/login fails closed (503), mirroring api_key's
    # unset-503 precedent. see docs/ARCHITECTURE.md.
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
    # exactly how an existing Eufy sync tool already runs safely, daily, unattended.
    eufy_email: str | None = None
    eufy_password: str | None = None
    eufy_device_id: str | None = None
    eufy_customer_id: str | None = None

    # --- Backup + restore automation (backup.py) ---
    # All optional and unset by default -- create_backup() logs and skips (not an error) when
    # unconfigured, same graceful-degradation contract as the Eufy block above. rsync over SSH to
    # a second host, not a cloud target: a self-hosted deployment typically has another machine
    # on its network to rsync to. The one-time SSH key exchange
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

    # --- Weekly/monthly email reports (email_reports.py, worker/main.py) ---
    # One shared SMTP relay for the whole deployment, not a per-athlete credential -- same
    # env-var, skip-when-unset contract as the Eufy/backup blocks above (email_reports.py's
    # scheduled jobs log and skip when `smtp_configured` is False). The recipient is each
    # athlete's own `athlete.email` (Settings > Profile); opt-in is per-athlete via
    # `athlete_email_report_config`. Port 587 is mail submission and uses STARTTLS ("starttls");
    # implicit TLS is port 465 ("ssl"). "none" is for a local unauthenticated relay only.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    # When the reports go out, resolved against `schedule_timezone` like every other worker job.
    # The athlete picked Sunday 18:00 for the weekly (week just ended + coming week's plan) and
    # the month's last day 18:00 for the monthly (day="last", not exposed as its own env var).
    email_report_hour: int = 18
    email_report_minute: int = 0
    email_report_weekly_day_of_week: str = "sun"

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

    def kaya_tokenstore_dir_for(self, athlete_id: str) -> Path:
        """Per-athlete Kaya token directory (tokens only, never the password); see
        docs/ARCHITECTURE.md."""
        return self.data_dir / "kaya_tokens" / athlete_id

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def backup_known_hosts_path(self) -> Path:
        # A dedicated known_hosts file under /data (always writable), not backup.py's own
        # read-only-mounted SSH key directory -- see backup.py::_rsync's own docstring for why
        # accept-new's first-contact write needs a genuinely writable path once the worker
        # container's root filesystem is read-only (container hardening, docs/ARCHITECTURE.md).
        return self.data_dir / "backup_known_hosts"

    @property
    def smtp_configured(self) -> bool:
        """All four of host/username/password/from present -- email_reports.py's scheduled jobs
        and the test endpoint check this before attempting any send."""
        return bool(self.smtp_host and self.smtp_username and self.smtp_password and self.smtp_from)

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.cors_allowed_origins:
            return []
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
