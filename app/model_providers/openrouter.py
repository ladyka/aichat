from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
from fastapi import HTTPException

from app.config import get_settings
from app.telemetry import llm_byte_stream, llm_instrument


def _headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENROUTER_API_KEY is not configured",
        )
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }


@llm_instrument("openrouter.chat.completions")
async def chat_completions(payload: dict[str, Any]) -> httpx.Response:
    settings = get_settings()
    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, headers=_headers(), json=payload)
    return response


async def stream_chat_completions(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    """Stream OpenRouter SSE bytes.

    LLM span is opened with ``start_span`` (not as current). The OpenInference
    ``@llm`` decorator attaches a context token on first ``__anext__`` and
    detaches on generator close — that breaks when the tool loop peeks the
    stream under ``chain_span`` / ``using_attributes`` and Starlette continues
    it after those managers have exited.
    """
    async for chunk in llm_byte_stream(
        "openrouter.chat.completions.stream",
        _stream_chat_completions_raw(payload),
        input_payload=payload,
    ):
        yield chunk


async def _stream_chat_completions_raw(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    settings = get_settings()
    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
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
