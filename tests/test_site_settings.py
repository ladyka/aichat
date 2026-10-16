"""Админка и эффективные настройки моделей."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app import models_catalog as mc
from app import site_settings as ss
from app.config import Settings, get_settings
from app.db import SessionLocal
from tests.conftest import login, register


@pytest.fixture(autouse=True)
def _reset_site_settings_cache():
    ss.invalidate_overrides_cache()
    yield
    ss.invalidate_overrides_cache()
    mc.invalidate_models_cache()


def _email() -> str:
    return f"admin-{uuid.uuid4().hex[:8]}@example.com"


def test_parse_allowed_models_and_admin_emails(monkeypatch):
    monkeypatch.setenv("ALLOWED_MODELS", " openrouter/auto , ol/llama ")
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@Example.com, other@x.test")
    settings = Settings()
    assert settings.allowed_models == frozenset({"openrouter/auto", "ol/llama"})
    assert settings.admin_emails == frozenset({"boss@example.com", "other@x.test"})


def test_parse_allowed_models_empty(monkeypatch):
    monkeypatch.setenv("ALLOWED_MODELS", "")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    settings = Settings()
    assert settings.allowed_models is None
    assert settings.admin_emails == frozenset()


def test_effective_default_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(get_settings(), "default_model", "ol/demo")
    assert ss.get_effective_default_model() == "ol/demo"


def test_save_and_reset_model_settings(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "default_model", "default")
    db = SessionLocal()
    try:
        ss.save_model_settings(
            db,
            default_model="openrouter/auto",
            allowed_models=["openrouter/auto"],
        )
        assert ss.get_effective_default_model() == "openrouter/auto"
        assert ss.get_effective_allowed_models() == frozenset({"openrouter/auto"})

        ss.save_model_settings(
            db,
            default_model=None,
            allowed_models=[ss.ALLOWED_ALL_SENTINEL],
            use_env_default=True,
            use_env_allowed=False,
        )
        assert ss.get_effective_default_model() == "default"
        assert ss.get_effective_allowed_models() is None

        ss.reset_model_settings_to_env(db)
        assert ss.get_db_overrides() == (None, None)
    finally:
        db.close()


def test_allowlist_filters_catalog(monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "sk-test")
    monkeypatch.setattr(get_settings(), "default_model", "default")
    monkeypatch.setattr(ss, "_override_default", None)
    monkeypatch.setattr(ss, "_override_allowed", ["openrouter/auto"])
    raw = [
        {"id": "openrouter/auto:free", "created": 1},
        {"id": "other/free-model:free", "created": 2},
    ]
    payload, routes = mc._build_models_catalog(raw, [])
    payload, routes = mc._filter_by_allowlist(payload, routes)
    ids = [item["id"] for item in payload["data"]]
    assert ids[0] == mc.PUBLIC_DEFAULT_ID
    assert "openrouter/auto" in ids
    assert "other/free-model" not in ids
    assert "openrouter/auto" in routes


def test_resolve_model_rejects_disallowed(monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "sk-test")
    monkeypatch.setattr(ss, "_override_default", None)
    monkeypatch.setattr(ss, "_override_allowed", ["openrouter/auto"])
    with pytest.raises(HTTPException) as exc:
        mc.resolve_model("other/free-model")
    assert exc.value.status_code == 400


def test_admin_page_requires_admin(client, monkeypatch):
    address = _email()
    register(client, address)
    login(client, address)
    assert client.get("/admin", follow_redirects=False).status_code == 303

    async def fake_providers():
        return ([{"id": "openrouter/auto:free", "created": 1}], [], {})

    assert client.get("/api/admin/models").status_code == 403

    monkeypatch.setattr(mc, "_fetch_provider_models", fake_providers)
    monkeypatch.setattr(get_settings(), "admin_emails", frozenset({address.lower()}))
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Админка" in response.text
    assert "/admin-ui/assets/admin.js" in response.text
    payload = client.get("/api/admin/models")
    assert payload.status_code == 200
    assert "openrouter/auto" in payload.json()["catalog_ids"]


def test_admin_save_allowlist(client, monkeypatch):
    address = _email()
    register(client, address)
    login(client, address)
    monkeypatch.setattr(get_settings(), "admin_emails", frozenset({address.lower()}))
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "sk-test")

    async def fake_providers():
        return (
            [
                {"id": "openrouter/auto:free", "created": 1},
                {"id": "vendor/other:free", "created": 2},
            ],
            [],
            {},
        )

    monkeypatch.setattr(mc, "_fetch_provider_models", fake_providers)

    response = client.put(
        "/api/admin/models",
        json={
            "use_env_default": False,
            "use_env_allowed": False,
            "default_model": "openrouter/auto",
            "unrestricted": False,
            "allowed_models": ["openrouter/auto"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["effective_default_model"] == "openrouter/auto"
    assert "openrouter/auto" in body["selected_model_ids"]
    assert ss.get_effective_default_model() == "openrouter/auto"
    assert ss.get_effective_allowed_models() == frozenset({"openrouter/auto"})

    models = asyncio.run(mc.get_models_list(force_refresh=True))
    ids = [item["id"] for item in models["data"]]
    assert "openrouter/auto" in ids
    assert "vendor/other" not in ids

    rejected = client.put(
        "/api/admin/models",
        json={
            "default_model": "missing/model",
            "allowed_models": ["missing/model"],
        },
    )
    assert rejected.status_code == 400

    reset = client.post("/api/admin/models/reset")
    assert reset.status_code == 200
    assert ss.get_db_overrides() == (None, None)
