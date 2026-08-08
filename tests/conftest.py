import os
import re
import tempfile

_tmpdir = tempfile.mkdtemp(prefix="aichat_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["OPENROUTER_BASE_URL"] = "https://openrouter.test/api/v1"
os.environ["MODELS_CACHE_TTL"] = "3600"

# Env must be set before the app package is imported (noqa: E402).
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def api_key(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test")
    return settings


@pytest.fixture()
def db():
    from app.db import SessionLocal

    session = SessionLocal()
    yield session
    session.close()


def register(client, email, password="secret123"):
    return client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "password2": password,
            "consent": "on",
        },
        follow_redirects=False,
    )


def login(client, email, password="secret123"):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


def create_token(client, name="default"):
    response = client.post("/tokens", data={"name": name})
    match = re.search(r'<code class="token-value">(aichat_[^<]+)</code>', response.text)
    return match.group(1) if match else None, response


@pytest.fixture()
def mock_models(monkeypatch):
    payload = {
        "object": "list",
        "data": [{"id": "default", "object": "model", "created": 1, "owned_by": "aichat"}],
    }

    async def fake_get_models(*args, **kwargs):
        return payload

    for mod in ("app.routes.api", "app.routes.pages", "app.routes.conversations"):
        monkeypatch.setattr(f"{mod}.get_models_list", fake_get_models)
    return payload
