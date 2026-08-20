import asyncio

import pytest
from fastapi import HTTPException

from app.model_providers.openrouter import (
    _headers,
    chat_completions,
    generate_image,
    stream_chat_completions,
)


class FakeResponse:
    def __init__(self, status_code=200, data=None, raw=b""):
        self.status_code = status_code
        self._data = data
        self._raw = raw

    def json(self):
        return self._data

    async def aread(self):
        return self._raw


class FakeAsyncClient:
    def __init__(self, *a, **kw):
        self.post_kwargs = None
        self.stream_kwargs = None
        self._chunks = [b'data: {"model":"openrouter/free"}\n\n', b"data: [DONE]\n\n"]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        self.post_kwargs = kw
        return FakeResponse(status_code=200, data={"ok": True, "model": "openrouter/free"})

    def stream(self, *a, **kw):
        self.stream_kwargs = kw

        class FakeStream:
            def __init__(self, parent):
                self.parent = parent
                self.status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def aread(self):
                return b""

            async def aiter_bytes(self):
                for chunk in self.parent._chunks:
                    yield chunk

        return FakeStream(self)


def test_headers_without_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openrouter_api_key", "")
    with pytest.raises(HTTPException) as exc:
        _headers()
    assert exc.value.status_code == 503


def test_headers_with_key(api_key):
    headers = _headers()
    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"] == "application/json"


def test_chat_completions(monkeypatch, api_key):
    monkeypatch.setattr("app.model_providers.openrouter.httpx.AsyncClient", FakeAsyncClient)
    response = asyncio.run(chat_completions({"model": "default"}))
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_chat_completions_missing_key_raises(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openrouter_api_key", "")
    with pytest.raises(HTTPException):
        asyncio.run(chat_completions({"model": "default"}))


async def _collect(agen):
    return [chunk async for chunk in agen]


def test_stream_chat_completions(monkeypatch, api_key):
    monkeypatch.setattr("app.model_providers.openrouter.httpx.AsyncClient", FakeAsyncClient)
    chunks = asyncio.run(_collect(stream_chat_completions({"model": "default"})))
    assert len(chunks) == 2
    assert b"[DONE]" in chunks[1]


def test_stream_chat_completions_error(monkeypatch, api_key):

    class ErrorClient(FakeAsyncClient):
        def stream(self, *a, **kw):
            class ErrorStream:
                status_code = 502

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def aread(self):
                    return b'{"error": "boom"}'

                async def aiter_bytes(self):
                    return iter([])

            return ErrorStream()

    monkeypatch.setattr("app.model_providers.openrouter.httpx.AsyncClient", ErrorClient)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_collect(stream_chat_completions({"model": "default"})))
    assert exc.value.status_code == 502
    assert exc.value.detail == {"error": "boom"}


def test_generate_image_success(monkeypatch, api_key):
    captured = {}

    class ImageClient(FakeAsyncClient):
        async def post(self, url, *a, **kw):
            captured["url"] = url
            captured["json"] = kw.get("json")
            return FakeResponse(
                status_code=200,
                data={
                    "data": [{"b64_json": "aGVsbG8=", "media_type": "image/png"}],
                    "usage": {"cost": 0.014},
                },
            )

    monkeypatch.setattr("app.model_providers.openrouter.httpx.AsyncClient", ImageClient)
    result = asyncio.run(generate_image("a cat", aspect_ratio="1:1"))
    assert result["bytes"] == b"hello"
    assert result["cost"] == 0.014
    assert result["model"] == "black-forest-labs/flux.2-klein-4b"
    assert captured["url"].endswith("/images")
    assert captured["json"]["prompt"] == "a cat"
    assert captured["json"]["aspect_ratio"] == "1:1"


def test_generate_image_http_error(monkeypatch, api_key):
    class ImageClient(FakeAsyncClient):
        async def post(self, url, *a, **kw):
            return FakeResponse(
                status_code=402,
                data={"error": {"message": "Insufficient credits"}},
            )

    monkeypatch.setattr("app.model_providers.openrouter.httpx.AsyncClient", ImageClient)
    result = asyncio.run(generate_image("a cat"))
    assert result["error"] == "Insufficient credits"
    assert result["status"] == 402
