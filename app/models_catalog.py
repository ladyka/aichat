from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx
from fastapi import HTTPException

from app.config import get_settings
from app.model_providers import e7by, ol

logger = logging.getLogger("aichat.models")

PUBLIC_DEFAULT_ID = "default"
UPSTREAM_DEFAULT_ID = "openrouter/free"
FREE_SUFFIX = ":free"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_E7_BY = e7by.PROVIDER_ID
E7_BY_PREFIX = f"{PROVIDER_E7_BY}/"
PROVIDER_OL = ol.PROVIDER_ID
OL_PREFIX = f"{PROVIDER_OL}/"

_cache_lock = asyncio.Lock()
_cache_payload: dict[str, Any] | None = None
_cache_expires_at = 0.0
_cache_public_ids: set[str] = set()
_cache_routes: dict[str, "ModelRoute"] = {}


@dataclass(frozen=True)
class ModelRoute:
    public_id: str
    provider: str
    upstream_id: str


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


def to_e7_public_id(upstream_id: str) -> str:
    model = (upstream_id or "").strip()
    if not model:
        return ""
    if model.startswith(E7_BY_PREFIX):
        return model
    return f"{E7_BY_PREFIX}{model.lstrip('/')}"


def to_e7_upstream_id(public_id: str) -> str:
    model = (public_id or "").strip()
    if model.startswith(E7_BY_PREFIX):
        return model[len(E7_BY_PREFIX) :]
    return model


def to_ol_public_id(upstream_id: str) -> str:
    """Upstream `deepseek-v4.1-flash:cloud` → публичный `ol/deepseek-v4.1-flash`."""
    model = (upstream_id or "").strip().lstrip("/")
    if model.startswith(OL_PREFIX):
        model = model[len(OL_PREFIX) :]
    if model.endswith(":cloud"):
        model = model[: -len(":cloud")]
    return f"{OL_PREFIX}{model}" if model else ""


def to_ol_upstream_id(public_id: str) -> str:
    """Публичный `ol/deepseek-v4.1-flash` → upstream `deepseek-v4.1-flash:cloud`."""
    model = (public_id or "").strip()
    if model.startswith(OL_PREFIX):
        model = model[len(OL_PREFIX) :]
    if not model:
        return ""
    if not model.endswith(":cloud"):
        return f"{model}:cloud"
    return model


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Content-Type": "application/json"}
    if settings.openrouter_api_key:
        headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"
    return headers


def _openai_model_item(
    model_id: str, created: int | None = None, owned_by: str = "openrouter"
) -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": created or int(time.time()),
        "owned_by": owned_by,
    }


def _created_int(raw: dict[str, Any]) -> int | None:
    created = raw.get("created")
    try:
        return int(created) if created is not None else None
    except (TypeError, ValueError):
        return None


def _build_models_response(
    openrouter_raw: list[dict[str, Any]],
    e7_raw: list[dict[str, Any]] | None = None,
    ol_raw: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload, _routes = _build_models_catalog(openrouter_raw, e7_raw or [], ol_raw or [])
    return payload


def _build_models_catalog(
    openrouter_raw: list[dict[str, Any]],
    e7_raw: list[dict[str, Any]],
    ol_raw: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, ModelRoute]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    routes: dict[str, ModelRoute] = {}

    items.append(_openai_model_item(PUBLIC_DEFAULT_ID, owned_by="aichat"))
    seen.add(PUBLIC_DEFAULT_ID)
    seen.add(UPSTREAM_DEFAULT_ID)
    routes[PUBLIC_DEFAULT_ID] = ModelRoute(
        public_id=PUBLIC_DEFAULT_ID,
        provider=PROVIDER_OPENROUTER,
        upstream_id=UPSTREAM_DEFAULT_ID,
    )

    for raw in openrouter_raw:
        upstream_id = str(raw.get("id") or "")
        if FREE_SUFFIX not in str(upstream_id):
            continue
        public_id = to_public_id(upstream_id)
        if not public_id or public_id in seen:
            continue
        owned_by = public_id.split("/", 1)[0] if "/" in public_id else PROVIDER_OPENROUTER
        items.append(_openai_model_item(public_id, created=_created_int(raw), owned_by=owned_by))
        seen.add(public_id)
        routes[public_id] = ModelRoute(
            public_id=public_id,
            provider=PROVIDER_OPENROUTER,
            upstream_id=(
                upstream_id if upstream_id.endswith(FREE_SUFFIX) else to_upstream_id(public_id)
            ),
        )

    for raw in e7_raw:
        upstream_id = str(raw.get("id") or raw.get("name") or "")
        public_id = to_e7_public_id(upstream_id)
        if not public_id or public_id in seen:
            continue
        items.append(
            _openai_model_item(public_id, created=_created_int(raw), owned_by=PROVIDER_E7_BY)
        )
        seen.add(public_id)
        routes[public_id] = ModelRoute(
            public_id=public_id,
            provider=PROVIDER_E7_BY,
            upstream_id=to_e7_upstream_id(public_id),
        )

    for raw in ol_raw:
        upstream_id = str(raw.get("id") or raw.get("name") or "")
        public_id = to_ol_public_id(upstream_id)
        if not public_id or public_id in seen:
            continue
        items.append(_openai_model_item(public_id, created=_created_int(raw), owned_by=PROVIDER_OL))
        seen.add(public_id)
        routes[public_id] = ModelRoute(
            public_id=public_id,
            provider=PROVIDER_OL,
            upstream_id=to_ol_upstream_id(public_id),
        )

    return {"object": "list", "data": items}, routes


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


async def _empty_models() -> list[dict[str, Any]]:
    return []


async def _fetch_provider_models() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    settings = get_settings()
    openrouter_task = asyncio.create_task(_fetch_openrouter_models())
    e7_task = asyncio.create_task(e7by.list_models()) if settings.e7_by_enabled else None
    ol_task = asyncio.create_task(ol.list_models()) if settings.ol_enabled else None
    openrouter_raw, e7_raw, ol_raw = await asyncio.gather(
        openrouter_task,
        e7_task or _empty_models(),
        ol_task or _empty_models(),
        return_exceptions=True,
    )

    if isinstance(openrouter_raw, BaseException):
        has_fallback = any(not isinstance(raw, BaseException) and raw for raw in (e7_raw, ol_raw))
        if not has_fallback:
            if isinstance(openrouter_raw, HTTPException):
                raise openrouter_raw
            raise HTTPException(
                status_code=502,
                detail="OpenRouter models error",
            ) from openrouter_raw
        logger.warning("OpenRouter models unavailable: %s", openrouter_raw)
        openrouter_raw = []

    if isinstance(e7_raw, BaseException):
        logger.warning("e7 models unavailable: %s", e7_raw)
        e7_raw = []

    if isinstance(ol_raw, BaseException):
        logger.warning("ol models unavailable: %s", ol_raw)
        ol_raw = []

    return openrouter_raw, e7_raw, ol_raw


async def get_models_list(*, force_refresh: bool = False) -> dict[str, Any]:
    global _cache_payload, _cache_expires_at, _cache_public_ids, _cache_routes

    now = time.time()
    if not force_refresh and _cache_payload is not None and now < _cache_expires_at:
        return _cache_payload

    async with _cache_lock:
        now = time.time()
        if not force_refresh and _cache_payload is not None and now < _cache_expires_at:
            return _cache_payload

        openrouter_raw, e7_raw, ol_raw = await _fetch_provider_models()
        payload, routes = _build_models_catalog(openrouter_raw, e7_raw, ol_raw)
        settings = get_settings()
        _cache_payload = payload
        _cache_expires_at = now + settings.models_cache_ttl
        _cache_public_ids = {item["id"] for item in payload["data"]}
        _cache_routes = routes
        return payload


def resolve_model(public_id: str | None) -> ModelRoute:
    settings = get_settings()
    requested = (public_id or "").strip() or settings.default_model

    if requested in _cache_routes:
        return _cache_routes[requested]

    if requested.startswith(E7_BY_PREFIX):
        if not settings.e7_by_enabled:
            raise HTTPException(status_code=503, detail="E7_BY_BASE_URL is not configured")
        upstream = to_e7_upstream_id(requested)
        if not upstream:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{requested}' is not available",
            )
        if _cache_public_ids and requested not in _cache_public_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{requested}' is not available",
            )
        return ModelRoute(
            public_id=requested,
            provider=PROVIDER_E7_BY,
            upstream_id=upstream,
        )

    if requested.startswith(OL_PREFIX):
        if not settings.ol_enabled:
            raise HTTPException(status_code=503, detail="OLLAMA_API_KEY is not configured")
        upstream = to_ol_upstream_id(requested)
        if not upstream:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{requested}' is not available",
            )
        # Не сверяемся с кешем каталога: ollama.com отдаёт только часть имён
        # (без latest-алиасов), а облако само вернёт ошибку для несуществующей модели.
        return ModelRoute(
            public_id=requested,
            provider=PROVIDER_OL,
            upstream_id=upstream,
        )

    upstream = to_upstream_id(requested)
    if upstream == UPSTREAM_DEFAULT_ID:
        return ModelRoute(
            public_id=PUBLIC_DEFAULT_ID,
            provider=PROVIDER_OPENROUTER,
            upstream_id=UPSTREAM_DEFAULT_ID,
        )

    public = to_public_id(upstream)
    if _cache_public_ids and public not in _cache_public_ids and requested not in _cache_public_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{requested}' is not available",
        )
    return ModelRoute(
        public_id=public,
        provider=PROVIDER_OPENROUTER,
        upstream_id=upstream,
    )


def resolve_upstream_model(public_id: str | None) -> str:
    return resolve_model(public_id).upstream_id
