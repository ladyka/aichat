from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value


@lru_cache
def get_settings() -> "Settings":
    return Settings()


class Settings:
    def __init__(self) -> None:
        self.root = ROOT
        self.openrouter_api_key = _env("OPENROUTER_API_KEY", "")
        self.openrouter_base_url = _env(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        )
        self.session_cookie = _env("SESSION_COOKIE", "aichat_session")
        self.session_days = int(_env("SESSION_DAYS", "30") or "30")
        self.database_url = self._database_url()
        self.default_model = _env("DEFAULT_MODEL", "default")
        self.models_cache_ttl = int(_env("MODELS_CACHE_TTL", "3600") or "3600")
        self.api_daily_limit = int(_env("API_DAILY_LIMIT", "10") or "10")
        self.max_tokens_per_user = int(_env("MAX_TOKENS_PER_USER", "10") or "10")
        self.share_ttl_days = int(_env("SHARE_TTL_DAYS", "30") or "30")
        self.openweather_api_key = _env("OPENWEATHER_API_KEY", "") or ""

        # Arize AX / Phoenix OTLP (see app/telemetry.py). Same vars as /tmp/aichat example.
        self.arize_space_id = _env("ARIZE_SPACE_ID", "") or ""
        self.arize_api_key = _env("ARIZE_API_KEY", "") or ""
        self.arize_project_name = _env("ARIZE_PROJECT_NAME", "aichat") or "aichat"
        # Example uses ARIZE_OTLP_ENDPOINT; arize-otel also reads ARIZE_COLLECTOR_ENDPOINT.
        self.arize_otlp_endpoint = (
            _env("ARIZE_OTLP_ENDPOINT")
            or _env("ARIZE_COLLECTOR_ENDPOINT")
            or ""
        )
        self.arize_enabled = bool(self.arize_space_id and self.arize_api_key)

    def _database_url(self) -> str:
        explicit = _env("DATABASE_URL")
        if explicit:
            return explicit

        # Use MySQL only when host is explicitly configured (production).
        mysql_host = _env("MYSQL_HOST")
        mysql_password = _env("MYSQL_PASSWORD")
        if mysql_host and mysql_password:
            user = _env("MYSQL_USER", "aichat")
            port = _env("MYSQL_PORT", "3306")
            database = _env("MYSQL_DATABASE", "aichat")
            return (
                f"mysql+pymysql://{user}:{mysql_password}"
                f"@{mysql_host}:{port}/{database}?charset=utf8mb4"
            )

        sqlite_path = ROOT / "aichat.db"
        return f"sqlite:///{sqlite_path}"
