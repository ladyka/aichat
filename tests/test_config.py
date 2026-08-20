from app.config import Settings


def test_settings_defaults(monkeypatch):
    for key in (
        "DATABASE_URL",
        "MYSQL_HOST",
        "MYSQL_PASSWORD",
        "OPENROUTER_API_KEY",
        "ARIZE_SPACE_ID",
        "ARIZE_API_KEY",
        "NEW_RELIC_LICENSE_KEY",
        "NEW_RELIC_USER_KEY",
        "E7_BY_BASE_URL",
        "E7_BY_API_KEY",
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
    assert not settings.new_relic_enabled
    assert not settings.e7_by_enabled
    assert settings.e7_by_base_url == ""


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


def test_settings_newrelic_defaults(monkeypatch):
    monkeypatch.delenv("NEW_RELIC_LICENSE_KEY", raising=False)
    monkeypatch.delenv("NEW_RELIC_USER_KEY", raising=False)
    settings = Settings()
    assert not settings.new_relic_enabled
    assert settings.new_relic_app_name == "aichat"
    assert settings.new_relic_user_key == ""


def test_settings_newrelic_enabled(monkeypatch):
    monkeypatch.setenv("NEW_RELIC_LICENSE_KEY", "secret")
    monkeypatch.setenv("NEW_RELIC_USER_KEY", "user-key")
    monkeypatch.setenv("NEW_RELIC_APP_NAME", "my-app")
    settings = Settings()
    assert settings.new_relic_enabled
    assert settings.new_relic_license_key == "secret"
    assert settings.new_relic_user_key == "user-key"
    assert settings.new_relic_app_name == "my-app"


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


def test_settings_pzz_flags(monkeypatch):
    monkeypatch.setenv("PZZ_ENABLED", "0")
    monkeypatch.setenv("PZZ_ORDERS_ENABLED", "false")
    monkeypatch.setenv("PRAVO_ENABLED", "0")
    settings = Settings()
    assert not settings.pzz_enabled
    assert not settings.pzz_orders_enabled
    assert not settings.pravo_enabled


def test_settings_e7_by_base_url(monkeypatch):
    monkeypatch.setenv("E7_BY_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("E7_BY_API_KEY", "ollama-key")
    monkeypatch.setenv("E7_BY_TIMEOUT", "120")
    settings = Settings()
    assert settings.e7_by_enabled
    assert settings.e7_by_base_url == "http://127.0.0.1:11434/v1"
    assert settings.e7_by_api_key == "ollama-key"
    assert settings.e7_by_timeout == 120.0

    monkeypatch.setenv("E7_BY_BASE_URL", "http://host.example/v1/")
    assert Settings().e7_by_base_url == "http://host.example/v1"
