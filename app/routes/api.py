from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import telemetry as telemetry_mod
from app.auth import get_user_from_api_token, get_user_from_session
from app.config import get_settings
from app.db import ApiToken, ApiTokenUsage, UsageLog, User, get_db
from app.model_providers import e7by
from app.model_providers.openrouter import chat_completions, stream_chat_completions
from app.models_catalog import (
    PROVIDER_E7_BY,
    PUBLIC_DEFAULT_ID,
    UPSTREAM_DEFAULT_ID,
    ModelRoute,
    get_models_list,
    resolve_model,
    to_e7_public_id,
    to_public_id,
)
from app.tools import call_tool, enabled_tools, extract_tool_calls

router = APIRouter()
logger = logging.getLogger("aichat.completions")

MAX_TOOL_STEPS = 5


def _trace_user(user: User, session_id: str):
    if telemetry_mod.tracer is None:
        return nullcontext()
    return telemetry_mod.request_context(str(user.id), session_id)


def _normalize_public_model(model: str | None) -> str:
    settings = get_settings()
    value = (model or "").strip() or settings.default_model
    if value in (UPSTREAM_DEFAULT_ID, PUBLIC_DEFAULT_ID):
        return PUBLIC_DEFAULT_ID
    return value


def _response_model_id(route: ModelRoute, reported: str | None) -> str:
    if route.public_id == PUBLIC_DEFAULT_ID:
        return PUBLIC_DEFAULT_ID
    if reported:
        if route.provider == PROVIDER_E7_BY:
            return to_e7_public_id(reported) or route.public_id
        return to_public_id(reported)
    return route.public_id


def _payload_from_body(body: dict[str, Any]) -> tuple[ModelRoute, dict[str, Any]]:
    public_model = _normalize_public_model(body.get("model"))
    route = resolve_model(public_model)
    payload: dict[str, Any] = {
        "model": route.upstream_id,
        "messages": list(body.get("messages") or []),
    }
    for key in (
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "stop",
        "tools",
        "tool_choice",
    ):
        if key in body and body[key] is not None:
            payload[key] = body[key]
    if body.get("stream"):
        payload["stream"] = True
    return route, payload


async def _call_chat(route: ModelRoute, payload: dict[str, Any]):
    if route.provider == PROVIDER_E7_BY:
        return await e7by.chat_completions(payload)
    return await chat_completions(payload)


def _stream_completions(route: ModelRoute, payload: dict[str, Any]) -> AsyncIterator[bytes]:
    if route.provider == PROVIDER_E7_BY:
        return e7by.stream_chat_completions(payload)
    return stream_chat_completions(payload)


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


def _api_usage_exceeded(db: Session, token: ApiToken) -> JSONResponse | None:
    """Enforce the per-token daily API limit; increment the counter if allowed."""
    settings = get_settings()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = db.scalar(
        select(ApiTokenUsage).where(
            ApiTokenUsage.token_id == token.id,
            ApiTokenUsage.day == today,
        )
    )
    if usage is None:
        usage = ApiTokenUsage(token_id=token.id, day=today, count=0)
        db.add(usage)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            usage = db.scalar(
                select(ApiTokenUsage).where(
                    ApiTokenUsage.token_id == token.id,
                    ApiTokenUsage.day == today,
                )
            )
    if usage is None or usage.count >= settings.api_daily_limit:
        return JSONResponse(
            status_code=429,
            content={
                "error": (
                    f"Дневной лимит API исчерпан: не более "
                    f"{settings.api_daily_limit} запросов в сутки на токен. "
                    "Лимит обновится завтра."
                )
            },
            headers={"Retry-After": "86400"},
        )
    usage.count += 1
    db.commit()
    return None


def _models_response(payload: dict[str, Any]) -> Response:
    settings = get_settings()
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": f"public, max-age={settings.models_cache_ttl}",
        },
    )


def _rewrite_sse_line(line: bytes, route: ModelRoute) -> bytes:
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
            route,
            reported if isinstance(reported, str) else None,
        )
        return f"data: {json.dumps(obj, ensure_ascii=False)}".encode("utf-8")
    return line


async def _public_event_stream(payload: dict[str, Any], route: ModelRoute) -> AsyncIterator[bytes]:
    buffer = b""
    async for chunk in _stream_completions(route, payload):
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield _rewrite_sse_line(line, route) + b"\n"
    if buffer:
        yield _rewrite_sse_line(buffer, route)


async def _tool_chat_response(
    route: ModelRoute,
    payload: dict[str, Any],
    source: str,
    db: Session,
    user: User,
    known_location: dict[str, float] | None = None,
) -> Response:
    """Internal chat only: loop model <-> tool execution.

    Non-stream requests answer via plain JSON. Streaming requests are peeked:
    we hold back the response only until we can tell whether the model asked
    for a tool (tool_calls deltas arrive first). If the model just answers in
    plain text, the buffered prefix is forwarded and the rest streams live —
    no extra upstream call, no perceived delay. If a tool is requested, we
    execute it and re-ask the model (streamed), then forward the final text.

    `get_user_location` is never executed server-side: if the browser already
    shared coordinates (`known_location`), the tool is resolved instantly;
    otherwise the loop pauses and asks the frontend to prompt the user via
    navigator.geolocation (`type: "location_request"` response).
    """
    messages = list(payload.get("messages") or [])
    location = _known_location(messages, known_location)
    if not payload.get("stream"):
        for _ in range(MAX_TOOL_STEPS):
            response = await _call_chat(route, payload)
            try:
                data = response.json()
            except Exception:
                return JSONResponse(
                    status_code=502,
                    content={"error": "Invalid response from upstream"},
                )
            if response.status_code >= 400:
                return JSONResponse(status_code=response.status_code, content=data)
            if isinstance(data, dict) and isinstance(data.get("model"), str):
                data = {**data, "model": _response_model_id(route, data.get("model"))}
            message = (data.get("choices") or [{}])[0].get("message") or {}
            if not isinstance(message, dict) or not message.get("tool_calls"):
                _log_usage(db, user, route.public_id, source, data.get("usage"))
                return JSONResponse(content=data, status_code=response.status_code)
            requests_location = any(
                call.get("function", {}).get("name") == "get_user_location"
                for call in message["tool_calls"]
            )
            if requests_location and not location:
                return JSONResponse(
                    content={
                        "type": "location_request",
                        "assistant_tool_call": message,
                    }
                )
            messages.append(message)
            for call in message["tool_calls"]:
                if call.get("function", {}).get("name") == "get_user_location":
                    result = json.dumps(location, ensure_ascii=False)
                else:
                    result = await call_tool(
                        call["function"]["name"],
                        call["function"]["arguments"],
                        user=user,
                        db=db,
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    }
                )
            location = _known_location(messages, known_location)
            payload = {**payload, "messages": messages}
        return JSONResponse(
            status_code=502,
            content={"error": "Tool loop limit exceeded"},
        )

    for _ in range(MAX_TOOL_STEPS):
        stream = _stream_completions(route, payload)
        prefix = b""
        classified: str | None = None
        async for chunk in stream:
            prefix += chunk
            if extract_tool_calls(prefix):
                classified = "tool"
                break
            if _sse_has_content(prefix):
                classified = "text"
                break
        if classified is None:
            # Stream finished with no content and no tool calls.
            return StreamingResponse(
                _rewrite_chunks(_prefix_then(prefix, stream), route),
                media_type="text/event-stream",
            )
        if classified == "text":
            # Plain answer: forward the buffered prefix, stream the rest live.
            return StreamingResponse(
                _rewrite_chunks(_prefix_then(prefix, stream), route),
                media_type="text/event-stream",
            )

        raw = prefix
        async for chunk in stream:
            raw += chunk
        calls = extract_tool_calls(raw)
        requests_location = any(call["name"] == "get_user_location" for call in calls)
        if requests_location and not location:
            assistant_message = _tool_call_messages(calls)[0]
            return StreamingResponse(
                _location_request_stream(assistant_message),
                media_type="text/event-stream",
            )
        messages.extend(_tool_call_messages(calls))
        for call in calls:
            if call["name"] == "get_user_location":
                result = json.dumps(location, ensure_ascii=False)
            else:
                result = await call_tool(call["name"], call["arguments"], user=user, db=db)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        location = _known_location(messages, known_location)
        payload = {**payload, "messages": messages, "stream": True}

    return JSONResponse(
        status_code=502,
        content={"error": "Tool loop limit exceeded"},
    )


async def _prefix_then(prefix: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    yield prefix
    async for chunk in rest:
        yield chunk


async def _rewrite_chunks(chunks: AsyncIterator[bytes], route: ModelRoute) -> AsyncIterator[bytes]:
    buffer = b""
    async for chunk in chunks:
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield _rewrite_sse_line(line, route) + b"\n"
    if buffer:
        yield _rewrite_sse_line(buffer, route)


def _sse_has_content(raw: bytes) -> bool:
    for line in raw.split(b"\n"):
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            continue
        data = stripped[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        delta = (obj.get("choices") or [{}])[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            return True
    return False


def _tool_call_messages(calls: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Convert aggregated SSE tool_calls into one assistant message."""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in calls
            ],
        }
    ]


def _body_location(body: dict[str, Any]) -> dict[str, float] | None:
    """Coordinates shared by the browser in the /api/chat body."""
    location = body.get("location")
    if not isinstance(location, dict):
        return None
    try:
        return {
            "lat": float(location.get("lat")),
            "lon": float(location.get("lon")),
        }
    except (TypeError, ValueError):
        return None


def _known_location(
    messages: list[dict[str, Any]],
    body_location: dict[str, float] | None,
) -> dict[str, float] | None:
    """Latest known coordinates: from the body or a prior get_user_location result."""
    location = dict(body_location) if body_location else None
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        try:
            content = json.loads(msg.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(content, dict) and "lat" in content and "lon" in content:
            try:
                location = {
                    "lat": float(content["lat"]),
                    "lon": float(content["lon"]),
                }
            except (TypeError, ValueError):
                continue
    return location


async def _location_request_stream(
    assistant_message: dict[str, Any],
) -> AsyncIterator[bytes]:
    event = {
        "type": "location_request",
        "assistant_tool_call": assistant_message,
    }
    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


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
    conversation_id = body.get("conversation_id")
    session_id = (
        conversation_id.strip()
        if isinstance(conversation_id, str) and conversation_id.strip()
        else uuid4().hex
    )
    with _trace_user(user, session_id):
        return await _proxy_inner(user, body, source, db, session_id)


async def _proxy_inner(
    user: User,
    body: dict[str, Any],
    source: str,
    db: Session,
    session_id: str,
):
    # Prefer explicit model; UI chat falls back to user preference.
    if source == "chat" and not (body.get("model") or "").strip():
        preferred = (getattr(user, "preferred_model", None) or "").strip()
        if preferred:
            body = {**body, "model": preferred}

    # Internal chat only: advertise the enabled tools to the model.
    known_location: dict[str, float] | None = None
    if source == "chat":
        tools = enabled_tools()
        if tools:
            body = {**body, "tools": tools}
        known_location = _body_location(body)

    route, payload = _payload_from_body(body)

    logger.info(
        "completions user_id=%s source=%s aichat_model=%s provider=%s upstream_model=%s",
        user.id,
        source,
        route.public_id,
        route.provider,
        route.upstream_id,
    )

    # Internal chat: run the tool loop server-side, hand back the final text.
    if source == "chat" and payload.get("tools"):
        with telemetry_mod.chain_span("chat.tool_loop"):
            return await _tool_chat_response(route, payload, source, db, user, known_location)

    if payload.get("stream"):
        return StreamingResponse(
            telemetry_mod.stream_in_session(session_id, _public_event_stream(payload, route)),
            media_type="text/event-stream",
        )

    response = await _call_chat(route, payload)
    try:
        data = response.json()
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"error": "Invalid response from upstream"},
        )
    if response.status_code >= 400:
        return JSONResponse(status_code=response.status_code, content=data)

    if isinstance(data, dict) and isinstance(data.get("model"), str):
        data = {
            **data,
            "model": _response_model_id(route, data.get("model")),
        }

    _log_usage(
        db, user, route.public_id, source, data.get("usage") if isinstance(data, dict) else None
    )
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
    user, token = get_user_from_api_token(db, authorization)
    usage_error = _api_usage_exceeded(db, token)
    if usage_error:
        return usage_error
    body = await request.json()
    return await _proxy(user, body, "api", db)
