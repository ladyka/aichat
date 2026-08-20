from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
from fastapi import HTTPException

from app.config import get_settings
from app.telemetry import llm_byte_stream, llm_instrument

logger = logging.getLogger("aichat.openrouter")


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


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code")
            if msg:
                return str(msg)
        if isinstance(err, str) and err:
            return err
        if payload.get("message"):
            return str(payload["message"])
    return fallback


async def generate_image(
    prompt: str,
    *,
    aspect_ratio: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """POST /images on OpenRouter. Returns bytes + mime or an error dict."""
    settings = get_settings()
    body: dict[str, Any] = {
        "model": (model or settings.image_generation_model).strip(),
        "prompt": prompt,
        "output_format": "png",
    }
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio

    url = f"{settings.openrouter_base_url.rstrip('/')}/images"
    try:
        headers = _headers()
    except HTTPException as exc:
        return {"error": str(exc.detail), "status": exc.status_code}
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, headers=headers, json=body)

    try:
        data = response.json()
    except Exception:
        text = response.text if hasattr(response, "text") else ""
        return {
            "error": _error_message(None, text or f"OpenRouter images HTTP {response.status_code}"),
            "status": response.status_code,
        }

    if response.status_code >= 400:
        return {
            "error": _error_message(data, f"OpenRouter images HTTP {response.status_code}"),
            "status": response.status_code,
        }

    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return {"error": "OpenRouter не вернул изображение", "status": 502}
    first = items[0]
    if not isinstance(first, dict):
        return {"error": "OpenRouter не вернул изображение", "status": 502}
    raw_b64 = first.get("b64_json")
    if not isinstance(raw_b64, str) or not raw_b64.strip():
        return {"error": "OpenRouter не вернул изображение", "status": 502}
    try:
        image_bytes = base64.b64decode(raw_b64)
    except Exception:
        return {"error": "Не удалось декодировать изображение", "status": 502}
    if not image_bytes:
        return {"error": "Пустое изображение от OpenRouter", "status": 502}

    media_type = str(first.get("media_type") or "image/png")
    usage = data.get("usage") if isinstance(data, dict) else None
    cost = None
    if isinstance(usage, dict) and usage.get("cost") is not None:
        cost = usage.get("cost")
    logger.info(
        "openrouter.images model=%s status=%s bytes=%s cost=%s",
        body["model"],
        response.status_code,
        len(image_bytes),
        cost,
    )
    return {
        "bytes": image_bytes,
        "media_type": media_type,
        "cost": cost,
        "model": body["model"],
    }
