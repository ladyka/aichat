from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import nullcontext
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_user_from_api_token, get_user_from_session
from app.config import get_settings
from app.db import UsageLog, User, get_db
from app.models_catalog import (
    PUBLIC_DEFAULT_ID,
    UPSTREAM_DEFAULT_ID,
    get_models_list,
    resolve_upstream_model,
    to_public_id,
)
from app.openrouter import chat_completions, stream_chat_completions
from app import telemetry as telemetry_mod

router = APIRouter()
logger = logging.getLogger("aichat.completions")


def _trace_user(user: User):
    if telemetry_mod.tracer is None:
        return nullcontext()
    from openinference.instrumentation import using_attributes

    return using_attributes(user_id=str(user.id))


def _normalize_public_model(model: str | None) -> str:
    settings = get_settings()
    value = (model or "").strip() or settings.default_model
    if value in (UPSTREAM_DEFAULT_ID, PUBLIC_DEFAULT_ID):
        return PUBLIC_DEFAULT_ID
    return value


def _response_model_id(requested_public: str, reported: str | None) -> str:
    if requested_public == PUBLIC_DEFAULT_ID:
        return PUBLIC_DEFAULT_ID
    if reported:
        return to_public_id(reported)
    return requested_public


def _payload_from_body(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    public_model = _normalize_public_model(body.get("model"))
    payload: dict[str, Any] = {
        "model": resolve_upstream_model(public_model),
        "messages": body.get("messages") or [],
    }
    for key in (
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "stop",
    ):
        if key in body and body[key] is not None:
            payload[key] = body[key]
    if body.get("stream"):
        payload["stream"] = True
    return public_model, payload


def _log_usage(db: Session, user: User, model: str, source: str, usage: dict | None) -> None:
    if not usage:
        return
    db.add(
        UsageLog(
            user_id=user.id,
            model=model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            source=source,
        )
    )
    db.commit()


def _models_response(payload: dict[str, Any]) -> Response:
    settings = get_settings()
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": f"public, max-age={settings.models_cache_ttl}",
        },
    )


def _rewrite_sse_line(line: bytes, requested_public: str) -> bytes:
    text = line.decode("utf-8", errors="replace")
    stripped = text.strip()
    if not stripped.startswith("data:"):
        return line
    data = stripped[5:].strip()
    if not data or data == "[DONE]":
        return line
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return line
    if isinstance(obj, dict) and "model" in obj:
        reported = obj.get("model")
        obj["model"] = _response_model_id(
            requested_public,
            reported if isinstance(reported, str) else None,
        )
        return f"data: {json.dumps(obj, ensure_ascii=False)}".encode("utf-8")
    return line


async def _public_event_stream(
    payload: dict[str, Any], requested_public: str
) -> AsyncIterator[bytes]:
    buffer = b""
    async for chunk in stream_chat_completions(payload):
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield _rewrite_sse_line(line, requested_public) + b"\n"
    if buffer:
        yield _rewrite_sse_line(buffer, requested_public)


@router.get("/v1/models")
async def v1_models():
    payload = await get_models_list()
    return _models_response(payload)


@router.get("/api/models")
async def api_models(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    payload = await get_models_list()
    return _models_response(payload)


async def _proxy(user: User, body: dict[str, Any], source: str, db: Session):
    with _trace_user(user):
        return await _proxy_inner(user, body, source, db)


async def _proxy_inner(user: User, body: dict[str, Any], source: str, db: Session):
    # Prefer explicit model; UI chat falls back to user preference.
    if source == "chat" and not (body.get("model") or "").strip():
        preferred = (getattr(user, "preferred_model", None) or "").strip()
        if preferred:
            body = {**body, "model": preferred}

    public_model, payload = _payload_from_body(body)
    upstream_model = str(payload.get("model"))

    logger.info(
        "completions user_id=%s source=%s aichat_model=%s openrouter_model=%s",
        user.id,
        source,
        public_model,
        upstream_model,
    )

    if payload.get("stream"):
        return StreamingResponse(
            _public_event_stream(payload, public_model),
            media_type="text/event-stream",
        )

    response = await chat_completions(payload)
    try:
        data = response.json()
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"error": "Invalid response from OpenRouter"},
        )
    if response.status_code >= 400:
        return JSONResponse(status_code=response.status_code, content=data)

    if isinstance(data, dict) and isinstance(data.get("model"), str):
        data = {
            **data,
            "model": _response_model_id(public_model, data.get("model")),
        }

    _log_usage(db, user, public_model, source, data.get("usage") if isinstance(data, dict) else None)
    return JSONResponse(content=data, status_code=response.status_code)


@router.post("/api/chat")
async def ui_chat(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    body = await request.json()
    return await _proxy(user, body, "chat", db)


@router.post("/v1/chat/completions")
async def v1_chat_completions(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    user = get_user_from_api_token(db, authorization)
    body = await request.json()
    return await _proxy(user, body, "api", db)
