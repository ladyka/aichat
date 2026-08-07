from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx2 as httpx
from fastapi import HTTPException

from app.config import get_settings

PUBLIC_DEFAULT_ID = "default"
UPSTREAM_DEFAULT_ID = "openrouter/free"
FREE_SUFFIX = ":free"

_cache_lock = asyncio.Lock()
_cache_payload: dict[str, Any] | None = None
_cache_expires_at = 0.0
_cache_public_ids: set[str] = set()


def to_public_id(upstream_id: str) -> str:
    if upstream_id == UPSTREAM_DEFAULT_ID:
        return PUBLIC_DEFAULT_ID
    if upstream_id.endswith(FREE_SUFFIX):
        return upstream_id[: -len(FREE_SUFFIX)]
    return upstream_id


def to_upstream_id(public_id: str) -> str:
    model = (public_id or "").strip() or PUBLIC_DEFAULT_ID
    if model in (PUBLIC_DEFAULT_ID, UPSTREAM_DEFAULT_ID):
        return UPSTREAM_DEFAULT_ID
    if model.endswith(FREE_SUFFIX):
        return model
    return f"{model}{FREE_SUFFIX}"


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Content-Type": "application/json"}
    if settings.openrouter_api_key:
        headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"
    return headers


def _openai_model_item(model_id: str, created: int | None = None, owned_by: str = "openrouter") -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": created or int(time.time()),
        "owned_by": owned_by,
    }


def _build_models_response(raw_models: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    items.append(_openai_model_item(PUBLIC_DEFAULT_ID, owned_by="aichat"))
    seen.add(PUBLIC_DEFAULT_ID)
    seen.add(UPSTREAM_DEFAULT_ID)

    for raw in raw_models:
        upstream_id = str(raw.get("id") or "")
        if FREE_SUFFIX not in str(upstream_id):
            continue
        public_id = to_public_id(upstream_id)
        if not public_id or public_id in seen:
            continue
        created = raw.get("created")
        try:
            created_int = int(created) if created is not None else None
        except (TypeError, ValueError):
            created_int = None
        owned_by = public_id.split("/", 1)[0] if "/" in public_id else "openrouter"
        items.append(_openai_model_item(public_id, created=created_int, owned_by=owned_by))
        seen.add(public_id)

    return {"object": "list", "data": items}


async def _fetch_openrouter_models() -> list[dict[str, Any]]:
    settings = get_settings()
    url = f"{settings.openrouter_base_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url, headers=_headers())
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter models error: {response.status_code}",
        )
    data = response.json()
    models = data.get("data")
    if not isinstance(models, list):
        raise HTTPException(status_code=502, detail="Invalid OpenRouter models response")
    return models


async def get_models_list(*, force_refresh: bool = False) -> dict[str, Any]:
    global _cache_payload, _cache_expires_at, _cache_public_ids

    now = time.time()
    if not force_refresh and _cache_payload is not None and now < _cache_expires_at:
        return _cache_payload

    async with _cache_lock:
        now = time.time()
        if not force_refresh and _cache_payload is not None and now < _cache_expires_at:
            return _cache_payload

        raw = await _fetch_openrouter_models()
        payload = _build_models_response(raw)
        settings = get_settings()
        _cache_payload = payload
        _cache_expires_at = now + settings.models_cache_ttl
        _cache_public_ids = {item["id"] for item in payload["data"]}
        return payload


def resolve_upstream_model(public_id: str | None) -> str:
    settings = get_settings()
    requested = (public_id or "").strip() or settings.default_model
    upstream = to_upstream_id(requested)

    if upstream == UPSTREAM_DEFAULT_ID:
        return UPSTREAM_DEFAULT_ID

    public = to_public_id(upstream)
    if _cache_public_ids and public not in _cache_public_ids and requested not in _cache_public_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{requested}' is not available",
        )

    return upstream
