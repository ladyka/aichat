import asyncio

import pytest
from fastapi import HTTPException

from app.model_providers import e7by


def test_list_models_disabled(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "e7_by_enabled", False)
    assert asyncio.run(e7by.list_models()) == []


def test_chat_requires_config(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "e7_by_enabled", False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(e7by.chat_completions({"model": "llama3"}))
    assert exc.value.status_code == 503


def test_native_base_url():
    assert e7by._native_base_url("http://x:11434/v1") == "http://x:11434"
    assert e7by._native_base_url("http://x:11434") == "http://x:11434"


def test_headers_with_optional_key(monkeypatch, api_key):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "e7_by_api_key", "")
    headers = e7by._headers()
    assert "Authorization" not in headers
    monkeypatch.setattr(get_settings(), "e7_by_api_key", "secret")
    headers = e7by._headers()
    assert headers["Authorization"] == "Bearer secret"


def test_list_models_openai_compat(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "e7_by_enabled", True)
    monkeypatch.setattr(get_settings(), "e7_by_base_url", "http://ollama.test/v1")
    monkeypatch.setattr(get_settings(), "e7_by_api_key", "")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"id": "llama3.2:latest"}]}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(e7by.httpx, "AsyncClient", FakeClient)
    models = asyncio.run(e7by.list_models())
    assert models == [{"id": "llama3.2:latest"}]
