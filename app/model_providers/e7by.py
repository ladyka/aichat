from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
from fastapi import HTTPException

from app.config import get_settings
from app.telemetry import llm_byte_stream, llm_instrument

PROVIDER_ID = "e7.by"


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Content-Type": "application/json"}
    if settings.e7_by_api_key:
        headers["Authorization"] = f"Bearer {settings.e7_by_api_key}"
    return headers


def _require_configured() -> None:
    settings = get_settings()
    if not settings.e7_by_enabled:
        raise HTTPException(
            status_code=503,
            detail="E7_BY_BASE_URL is not configured",
        )


def _native_base_url(openai_base: str) -> str:
    base = openai_base.rstrip("/")
    if base.endswith("/v1"):
        return base[: -len("/v1")].rstrip("/")
    return base


async def list_models() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.e7_by_enabled:
        return []

    openai_url = f"{settings.e7_by_base_url.rstrip('/')}/models"
    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(openai_url, headers=_headers())
        if response.status_code < 400:
            try:
                data = response.json()
            except Exception:
                data = None
            if isinstance(data, dict):
                models = data.get("data")
                if isinstance(models, list):
                    return models

        tags_url = f"{_native_base_url(settings.e7_by_base_url)}/api/tags"
        tags_response = await client.get(tags_url, headers=_headers())

    if tags_response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"e7.by models error: {response.status_code}",
        )
    try:
        payload = tags_response.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid e7.by models response")
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise HTTPException(status_code=502, detail="Invalid e7.by models response")
    items: list[dict[str, Any]] = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("name") or raw.get("model") or "")
        if model_id:
            items.append({"id": model_id})
    return items


@llm_instrument("e7.by.chat.completions")
async def chat_completions(payload: dict[str, Any]) -> httpx.Response:
    _require_configured()
    settings = get_settings()
    url = f"{settings.e7_by_base_url.rstrip('/')}/chat/completions"
    timeout = httpx.Timeout(settings.e7_by_timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=_headers(), json=payload)
    return response


async def stream_chat_completions(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    async for chunk in llm_byte_stream(
        "e7.by.chat.completions.stream",
        _stream_chat_completions_raw(payload),
        input_payload=payload,
    ):
        yield chunk


async def _stream_chat_completions_raw(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    _require_configured()
    settings = get_settings()
    url = f"{settings.e7_by_base_url.rstrip('/')}/chat/completions"
    body = {**payload, "stream": True}

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=_headers(), json=body) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                try:
                    detail = json.loads(error_body.decode("utf-8"))
                except Exception:
                    detail = {"error": error_body.decode("utf-8", errors="replace")}
                raise HTTPException(status_code=response.status_code, detail=detail)
            async for chunk in response.aiter_bytes():
                yield chunk
