import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text())["project"]["version"]


class Settings(BaseSettings):
    """All settings come from environment variables or backend/.env (never committed).

    `python -m app.setup_db` writes backend/.env with both database URLs for this computer.
    """

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_env: str = "development"
    # The app's own login: reads and writes rows, cannot change the schema or the audit log.
    database_url: str = "postgresql+psycopg://smet_app@127.0.0.1:5432/smet"
    # The schema owner, used only by migrations (and test clean-up). Empty = database_url.
    migration_database_url: str = ""
    db_pool_size: int = 5
    api_docs: bool = False  # Swagger UI loads scripts from a CDN, so it is off unless asked for
    # Host names the API answers to. Anything else is refused (DNS-rebinding protection).
    allowed_hosts: list[str] = ["127.0.0.1", "localhost"]
    cors_origins: list[str] = []  # the web app is served same-origin (Vite proxy / reverse proxy)
    session_ttl_hours: int = 10  # one working day; sign in again after
    session_idle_minutes: int = 60  # signed out after an hour without any request
    cookie_secure: bool = False  # must be true whenever the app is served over HTTPS
    run_worker_in_api: bool = True  # background job worker inside the API process
    # Photographs and reports: kept outside the code folder (on Windows, e.g. D:/smet-files).
    files_dir: str = str(BACKEND_DIR / "data" / "files")
    max_upload_mb: int = 10
    # The address printed in QR labels. Labels can't be reprinted cheaply, so set the real one first.
    public_base_url: str = "http://localhost:5173"
    # Encrypts maintenance reset codes (a Fernet key; `python -m app.setup_db` writes one). Losing it
    # makes recorded codes unreadable, so it is backed up with .env, never rotated by setup.
    codes_key: str = ""
    # Encrypts message-provider credentials (passwords, tokens); written by setup like CODES_KEY.
    secrets_key: str = ""
    # The most any message channel may do: off (nothing leaves the server), sandbox (test recipients
    # only) or production. Changed only here, by whoever runs the server, never from the screen.
    external_sends: Literal["off", "sandbox", "production"] = "off"

    # Phase 8. Where file bytes go: this computer (local) or an S3-compatible bucket (s3, needs boto3).
    files_backend: Literal["local", "s3"] = "local"
    s3_endpoint_url: str = ""  # empty = AWS; or e.g. https://<account>.r2.cloudflarestorage.com
    s3_bucket: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # A virus scanner reading the file on stdin, exit 0 clean / 1 infected, e.g.
    # "clamdscan --no-summary --fdpass -". Empty = no scan (recorded as "not scanned").
    malware_scan_command: str = ""
    # Reverse proxies in front of the API (addresses): only their X-Forwarded-For is believed, so rate
    # limits and the audit log see the real client address. Empty = nobody's.
    trusted_proxies: list[str] = []
    # Where `python -m app.backup create` writes; System health warns when the newest is old.
    backup_dir: str = str(BACKEND_DIR / "backups")

    @model_validator(mode="after")
    def _production_is_https(self):
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("APP_ENV=production needs COOKIE_SECURE=true (serve it over HTTPS)")
        return self

    @property
    def owner_url(self) -> str:
        return self.migration_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
