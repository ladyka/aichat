import asyncio

import pytest
from fastapi import HTTPException

from app import models_catalog as mc


def test_to_public_id():
    assert mc.to_public_id(mc.UPSTREAM_DEFAULT_ID) == mc.PUBLIC_DEFAULT_ID
    assert mc.to_public_id("openrouter/auto:free") == "openrouter/auto"
    assert mc.to_public_id("some/model") == "some/model"


def test_to_upstream_id():
    assert mc.to_upstream_id(None) == mc.UPSTREAM_DEFAULT_ID
    assert mc.to_upstream_id("") == mc.UPSTREAM_DEFAULT_ID
    assert mc.to_upstream_id(mc.PUBLIC_DEFAULT_ID) == mc.UPSTREAM_DEFAULT_ID
    assert mc.to_upstream_id("openrouter/auto") == "openrouter/auto:free"
    assert mc.to_upstream_id("openrouter/auto:free") == "openrouter/auto:free"


def test_openai_model_item():
    item = mc._openai_model_item("m", created=123)
    assert item["id"] == "m"
    assert item["object"] == "model"
    assert item["created"] == 123
    assert item["owned_by"] == "openrouter"


def test_build_models_response_filters_and_dedupes():
    raw = [
        {"id": "openrouter/auto:free", "created": 1},
        {"id": "vendor/paid-model", "created": 2},
        {"id": "openrouter/auto:free", "created": 3},
        {"id": "openrouter/auto"},
    ]
    payload = mc._build_models_response(raw)
    ids = [item["id"] for item in payload["data"]]
    assert payload["object"] == "list"
    assert ids[0] == mc.PUBLIC_DEFAULT_ID
    assert ids.count("openrouter/auto") == 1
    assert "vendor/paid-model" not in ids
    assert "openrouter/free" not in ids


def test_build_models_response_includes_e7_ids():
    raw = [{"id": "vendor/paid-model:free", "created": 1}]
    e7 = [{"id": "llama3.2:latest"}]
    payload = mc._build_models_response(raw, e7)
    ids = [item["id"] for item in payload["data"]]
    assert "e7/llama3.2:latest" in ids
    owned = {item["id"]: item["owned_by"] for item in payload["data"]}
    assert owned["e7/llama3.2:latest"] == "e7"


def test_headers_without_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openrouter_api_key", "")
    headers = mc._headers()
    assert "Authorization" not in headers


def test_headers_with_key(api_key):
    headers = mc._headers()
    assert headers["Authorization"] == "Bearer sk-test"


def test_get_models_list_cache_and_refresh(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return [{"id": "openrouter/auto:free", "created": 5}]

    monkeypatch.setattr(mc, "_fetch_openrouter_models", fake_fetch)
    monkeypatch.setattr(mc, "_cache_payload", None)
    monkeypatch.setattr(mc, "_cache_expires_at", 0)
    monkeypatch.setattr(mc, "_cache_routes", {})

    async def run():
        first = await mc.get_models_list()
        second = await mc.get_models_list()
        forced = await mc.get_models_list(force_refresh=True)
        return first, second, forced

    first, second, forced = asyncio.run(run())
    assert first is second
    assert forced is not None
    assert calls["n"] == 2
    assert any(i["id"] == "openrouter/auto" for i in first["data"])


def test_fetch_openrouter_models_error(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return type("R", (), {"status_code": 500})()

    monkeypatch.setattr(mc.httpx, "AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mc._fetch_openrouter_models())
    assert exc.value.status_code == 502


def test_resolve_upstream_model(monkeypatch):
    monkeypatch.setattr(mc, "_cache_public_ids", set())
    assert mc.resolve_upstream_model("default") == mc.UPSTREAM_DEFAULT_ID

    monkeypatch.setattr(mc, "_cache_public_ids", {"openrouter/auto"})
    assert mc.resolve_upstream_model("openrouter/auto") == "openrouter/auto:free"

    with pytest.raises(HTTPException) as exc:
        mc.resolve_upstream_model("nope/model")
    assert exc.value.status_code == 400


def test_resolve_e7_model(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "e7_by_enabled", True)
    monkeypatch.setattr(mc, "_cache_public_ids", set())
    monkeypatch.setattr(mc, "_cache_routes", {})
    route = mc.resolve_model("e7/llama3.2:latest")
    assert route.provider == mc.PROVIDER_E7_BY
    assert route.upstream_id == "llama3.2:latest"
    assert mc.to_e7_public_id("llama3.2:latest") == "e7/llama3.2:latest"

    monkeypatch.setattr(get_settings(), "e7_by_enabled", False)
    with pytest.raises(HTTPException) as exc:
        mc.resolve_model("e7/llama3.2:latest")
    assert exc.value.status_code == 503


def test_to_ol_public_and_upstream_id():
    assert mc.to_ol_public_id("deepseek-v4.1-flash") == "ol/deepseek-v4.1-flash"
    assert mc.to_ol_public_id("deepseek-v4.1-flash:cloud") == "ol/deepseek-v4.1-flash"
    assert mc.to_ol_public_id("ol/deepseek-v4.1-flash") == "ol/deepseek-v4.1-flash"
    assert mc.to_ol_public_id("") == ""
    assert mc.to_ol_upstream_id("ol/deepseek-v4.1-flash") == "deepseek-v4.1-flash:cloud"
    assert mc.to_ol_upstream_id("deepseek-v4.1-flash") == "deepseek-v4.1-flash:cloud"
    assert mc.to_ol_upstream_id("deepseek-v4.1-flash:cloud") == "deepseek-v4.1-flash:cloud"


def test_build_models_response_includes_ol_ids():
    raw = [{"id": "vendor/paid-model:free", "created": 1}]
    ol_raw = [{"id": "deepseek-v4.1-flash", "created": 2}]
    payload = mc._build_models_response(raw, [], ol_raw)
    ids = [item["id"] for item in payload["data"]]
    assert "ol/deepseek-v4.1-flash" in ids
    owned = {item["id"]: item["owned_by"] for item in payload["data"]}
    assert owned["ol/deepseek-v4.1-flash"] == "ol"
    routes = mc._build_models_catalog(raw, [], ol_raw)[1]
    assert routes["ol/deepseek-v4.1-flash"].upstream_id == "deepseek-v4.1-flash:cloud"
    assert routes["ol/deepseek-v4.1-flash"].provider == mc.PROVIDER_OL


def test_resolve_ol_model(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ol_enabled", True)
    monkeypatch.setattr(mc, "_cache_public_ids", set())
    monkeypatch.setattr(mc, "_cache_routes", {})
    route = mc.resolve_model("ol/deepseek-v4.1-flash")
    assert route.provider == mc.PROVIDER_OL
    assert route.upstream_id == "deepseek-v4.1-flash:cloud"

    monkeypatch.setattr(get_settings(), "ol_enabled", False)
    with pytest.raises(HTTPException) as exc:
        mc.resolve_model("ol/deepseek-v4.1-flash")
    assert exc.value.status_code == 503
