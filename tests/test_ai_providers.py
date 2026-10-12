"""Дополнительные провайдеры из ai-providers.yaml."""

from __future__ import annotations

import asyncio

import pytest

from app import ai_providers as ap
from app import models_catalog as mc
from app.config import Settings, get_settings
from app.model_providers import openai_compat


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    ap.invalidate_yaml_providers_cache()
    mc.invalidate_models_cache()
    yield
    ap.invalidate_yaml_providers_cache()
    mc.invalidate_models_cache()


def test_expand_env(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "secret-value")
    assert ap._expand_env("Bearer ${DEMO_KEY}") == "Bearer secret-value"
    assert ap._expand_env("${MISSING_VAR}") == ""


def test_load_manual_and_auto_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gkey")
    path = tmp_path / "ai-providers.yaml"
    path.write_text(
        """
providers:
  - id: groq
    base_url: https://api.groq.com/openai/v1
    api_key: ${GROQ_API_KEY}
    enabled: true
  - id: local
    base_url: http://127.0.0.1:8000/v1
    api_key: ""
    models:
      - alpha
      - id: beta
    timeout: 30
  - id: e7
    base_url: http://example.com/v1
    api_key: x
  - id: Disabled
    base_url: http://x/v1
    enabled: false
""",
        encoding="utf-8",
    )
    providers = ap.load_yaml_providers(path)
    ids = [p.id for p in providers]
    assert ids == ["groq", "local"]
    groq = providers[0]
    assert groq.api_key == "gkey"
    assert not groq.use_manual_models
    assert groq.resolve_models_url() == "https://api.groq.com/openai/v1/models"
    local = providers[1]
    assert local.use_manual_models
    assert local.models == ("alpha", "beta")


def test_missing_file_is_empty(tmp_path):
    assert ap.load_yaml_providers(tmp_path / "nope.yaml") == ()


def test_build_catalog_includes_yaml_models(monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "sk-test")
    monkeypatch.setattr(get_settings(), "default_model", "default")
    yaml_raw = {"groq": [{"id": "llama-3.3-70b-versatile", "created": 9}]}
    payload, routes = mc._build_models_catalog([], [], [], yaml_raw)
    ids = [item["id"] for item in payload["data"]]
    assert "groq/llama-3.3-70b-versatile" in ids
    assert routes["groq/llama-3.3-70b-versatile"].provider == "groq"
    assert routes["groq/llama-3.3-70b-versatile"].upstream_id == "llama-3.3-70b-versatile"


def test_default_route_yaml_provider(monkeypatch, tmp_path):
    path = tmp_path / "ai-providers.yaml"
    path.write_text(
        """
providers:
  - id: groq
    base_url: https://api.groq.com/openai/v1
    api_key: k
    models: [llama-3.3-70b-versatile]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "ai_providers_file", str(path))
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "")
    monkeypatch.setattr(get_settings(), "default_model", "groq/llama-3.3-70b-versatile")
    ap.invalidate_yaml_providers_cache()
    route = mc._default_route()
    assert route is not None
    assert route.provider == "groq"
    assert route.upstream_id == "llama-3.3-70b-versatile"


def test_resolve_yaml_model(monkeypatch, tmp_path):
    path = tmp_path / "ai-providers.yaml"
    path.write_text(
        """
providers:
  - id: local
    base_url: http://127.0.0.1:9/v1
    models: [alpha]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "ai_providers_file", str(path))
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "")
    ap.invalidate_yaml_providers_cache()
    monkeypatch.setattr(mc, "_cache_routes", {})
    monkeypatch.setattr(mc, "_cache_public_ids", set())
    route = mc.resolve_model("local/alpha", enforce_allowlist=False)
    assert route.provider == "local"
    assert route.upstream_id == "alpha"


def test_list_models_manual_no_http(monkeypatch):
    provider = ap.YamlProvider(
        id="local",
        base_url="http://127.0.0.1:9/v1",
        models=("a", "b"),
    )

    async def boom(*_a, **_k):
        raise AssertionError("HTTP must not be called for manual models")

    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", boom)
    models = asyncio.run(openai_compat.list_models(provider))
    assert models == [{"id": "a"}, {"id": "b"}]


def test_list_models_fetches_url(monkeypatch):
    provider = ap.YamlProvider(
        id="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key="k",
    )

    class FakeResp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "llama-3.3-70b-versatile"}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            assert url.endswith("/models")
            assert headers["Authorization"] == "Bearer k"
            return FakeResp()

    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", FakeClient)
    models = asyncio.run(openai_compat.list_models(provider))
    assert models[0]["id"] == "llama-3.3-70b-versatile"


def test_settings_ai_providers_file(monkeypatch):
    monkeypatch.setenv("AI_PROVIDERS_FILE", "custom-providers.yaml")
    assert Settings().ai_providers_file == "custom-providers.yaml"


def test_get_models_list_includes_yaml(monkeypatch, tmp_path):
    path = tmp_path / "ai-providers.yaml"
    path.write_text(
        """
providers:
  - id: local
    base_url: http://127.0.0.1:9/v1
    models: [alpha]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "ai_providers_file", str(path))
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "sk-test")
    monkeypatch.setattr(get_settings(), "default_model", "default")
    ap.invalidate_yaml_providers_cache()

    async def fake_or():
        return [{"id": "openrouter/auto:free", "created": 1}]

    monkeypatch.setattr(mc, "_fetch_openrouter_models", fake_or)
    monkeypatch.setattr(get_settings(), "e7_by_enabled", False)
    monkeypatch.setattr(get_settings(), "ol_enabled", False)

    payload = asyncio.run(mc.get_models_list(force_refresh=True))
    ids = [item["id"] for item in payload["data"]]
    assert "local/alpha" in ids
    assert "openrouter/auto" in ids
