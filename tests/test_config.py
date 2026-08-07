from app.config import Settings


def test_settings_defaults(monkeypatch):
    for key in (
        "DATABASE_URL",
        "MYSQL_HOST",
        "MYSQL_PASSWORD",
        "OPENROUTER_API_KEY",
        "ARIZE_SPACE_ID",
        "ARIZE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    assert settings.database_url.startswith("sqlite:///")
    assert settings.session_cookie == "aichat_session"
    assert settings.session_days == 30
    assert settings.default_model == "default"
    assert settings.models_cache_ttl == 3600
    assert settings.api_daily_limit == 10
    assert settings.max_tokens_per_user == 10
    assert not settings.arize_enabled


def test_settings_database_url_explicit(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///custom.db")
    assert Settings().database_url == "sqlite:///custom.db"


def test_settings_database_url_mysql(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "db.example.com")
    monkeypatch.setenv("MYSQL_PASSWORD", "p@ss")
    settings = Settings()
    assert settings.database_url.startswith("mysql+pymysql://aichat:p@ss@db.example.com:3306/")
    assert "charset=utf8mb4" in settings.database_url


def test_settings_limits_parsed(monkeypatch):
    monkeypatch.setenv("API_DAILY_LIMIT", "3")
    monkeypatch.setenv("MAX_TOKENS_PER_USER", "2")
    assert Settings().api_daily_limit == 3
    assert Settings().max_tokens_per_user == 2


def test_settings_arize_enabled(monkeypatch):
    monkeypatch.delenv("ARIZE_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("ARIZE_COLLECTOR_ENDPOINT", raising=False)
    monkeypatch.setenv("ARIZE_SPACE_ID", "space")
    monkeypatch.setenv("ARIZE_API_KEY", "key")
    settings = Settings()
    assert settings.arize_enabled
    assert settings.arize_project_name == "aichat"
    assert settings.arize_otlp_endpoint == ""


def test_settings_custom_values(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE", "custom_cookie")
    monkeypatch.setenv("SESSION_DAYS", "7")
    monkeypatch.setenv("DEFAULT_MODEL", "my-model")
    monkeypatch.setenv("MODELS_CACHE_TTL", "60")
    settings = Settings()
    assert settings.session_cookie == "custom_cookie"
    assert settings.session_days == 7
    assert settings.default_model == "my-model"
    assert settings.models_cache_ttl == 60
