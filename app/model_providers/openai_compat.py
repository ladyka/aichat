"""OpenAI-совместимый HTTP-прокси для провайдеров из ``ai-providers.yaml``."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
from fastapi import HTTPException

from app.ai_providers import YamlProvider, get_yaml_provider
from app.telemetry import llm_byte_stream, llm_instrument


def _require_provider(provider_id: str) -> YamlProvider:
    provider = get_yaml_provider(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail=f"Провайдер {provider_id!r} не настроен в ai-providers.yaml",
        )
    return provider


def _headers(provider: YamlProvider) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    return headers


async def list_models(provider: YamlProvider) -> list[dict[str, Any]]:
    """Список моделей: ручной из yaml или GET models_url."""
    if provider.use_manual_models:
        return [{"id": model_id} for model_id in provider.models]

    url = provider.resolve_models_url()
    timeout = httpx.Timeout(min(30.0, max(5.0, provider.timeout)))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=_headers(provider))
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"{provider.id} models error: {response.status_code}",
        )
    try:
        data = response.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid {provider.id} models response",
        ) from exc

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        models = data.get("data")
        if isinstance(models, list):
            return [item for item in models if isinstance(item, dict)]
    raise HTTPException(
        status_code=502,
        detail=f"Invalid {provider.id} models response",
    )


@llm_instrument("openai_compat.chat.completions")
async def chat_completions(provider_id: str, payload: dict[str, Any]) -> httpx.Response:
    provider = _require_provider(provider_id)
    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    timeout = httpx.Timeout(provider.timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, headers=_headers(provider), json=payload)


async def stream_chat_completions(
    provider_id: str, payload: dict[str, Any]
) -> AsyncIterator[bytes]:
    async for chunk in llm_byte_stream(
        "openai_compat.chat.completions.stream",
        _stream_chat_completions_raw(provider_id, payload),
        input_payload=payload,
    ):
        yield chunk


async def _stream_chat_completions_raw(
    provider_id: str, payload: dict[str, Any]
) -> AsyncIterator[bytes]:
    provider = _require_provider(provider_id)
    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    body = {**payload, "stream": True}

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=_headers(provider), json=body) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                try:
                    detail = json.loads(error_body.decode("utf-8"))
                except Exception:
                    detail = {"error": error_body.decode("utf-8", errors="replace")}
                raise HTTPException(status_code=response.status_code, detail=detail)
            async for chunk in response.aiter_bytes():
                yield chunk
