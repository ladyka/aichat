from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx2 as httpx
from fastapi import HTTPException
from ollama import AsyncClient, ResponseError

from app.config import get_settings
from app.telemetry import llm_byte_stream, llm_instrument

PROVIDER_ID = "ol"

# Облачный инференс только: upstream-имя всегда отправляется с суффиксом :cloud.
CLOUD_SUFFIX = ":cloud"

logger = logging.getLogger("aichat.ol")


def _client() -> AsyncClient:
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.ol_api_key}"} if settings.ol_api_key else None
    # Плоский float: httpx2.Timeout не подходит для httpx внутри библиотеки ollama.
    return AsyncClient(host=settings.ol_host, headers=headers, timeout=settings.ol_timeout)


def _require_configured() -> None:
    settings = get_settings()
    if not settings.ol_enabled:
        raise HTTPException(
            status_code=503,
            detail="OLLAMA_API_KEY is not configured",
        )


async def list_models() -> list[dict[str, Any]]:
    """Каталог облачных моделей ollama.com (публичный GET /api/tags)."""
    settings = get_settings()
    if not settings.ol_enabled:
        return []
    try:
        response = await AsyncClient(host=settings.ol_host).list()
    except ResponseError as exc:
        raise HTTPException(status_code=502, detail=f"ol models error: {exc.error}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ol models error: {exc}") from exc
    items: list[dict[str, Any]] = []
    for model in response.models:
        model_id = str(getattr(model, "model", None) or getattr(model, "name", "") or "")
        if model_id:
            items.append({"id": model_id})
    return items


def _tool_arguments(raw: Any) -> dict[str, Any]:
    """OpenAI передаёт arguments JSON-строкой, ollama ждёт dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-сообщения → ollama: tool_calls без id, tool-сообщение с tool_name."""
    result: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            ollama_calls = []
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = _tool_arguments(function.get("arguments"))
                call_id = str(call.get("id") or "")
                if call_id:
                    call_names[call_id] = name
                ollama_calls.append({"function": {"name": name, "arguments": arguments}})
            result.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": ollama_calls,
                }
            )
        elif role == "tool":
            result.append(
                {
                    "role": "tool",
                    "content": msg.get("content") or "",
                    "tool_name": call_names.get(str(msg.get("tool_call_id") or ""), ""),
                }
            )
        else:
            content = msg.get("content")
            result.append(
                {
                    "role": role or "user",
                    "content": content if isinstance(content, str) else "",
                }
            )
    return result


def _to_ollama_options(payload: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        if payload.get(key) is not None:
            options[key] = payload[key]
    if payload.get("max_tokens") is not None:
        options["num_predict"] = payload["max_tokens"]
    if payload.get("stop"):
        options["stop"] = payload["stop"]
    return options


def _to_ollama_request(payload: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": payload.get("model") or "",
        "messages": _to_ollama_messages(payload.get("messages") or []),
    }
    options = _to_ollama_options(payload)
    if options:
        request["options"] = options
    if payload.get("tools"):
        request["tools"] = payload["tools"]
    if payload.get("think") is not None:
        # Управление рассуждениями thinking-моделей (ollama: "think").
        request["think"] = bool(payload["think"])
    return request


def _to_openai_response(model: str, response: Any) -> dict[str, Any]:
    """ChatResponse ollama → OpenAI-совместимый chat.completion."""
    message = getattr(response, "message", None)
    content = getattr(message, "content", None) or ""
    raw_calls = getattr(message, "tool_calls", None) or []
    openai_calls = []
    for call in raw_calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not function:
            continue
        arguments = function.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        openai_calls.append(
            {
                "id": f"call_{uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": function.get("name") or "",
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
    assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
    if openai_calls:
        assistant_message["tool_calls"] = openai_calls
    prompt_tokens = int(getattr(response, "prompt_eval_count", 0) or 0)
    completion_tokens = int(getattr(response, "eval_count", 0) or 0)
    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": assistant_message,
                "finish_reason": "tool_calls" if openai_calls else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _sse_chunk(
    chunk_id: str,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> bytes:
    obj: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        obj["usage"] = usage
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


async def _stream_raw(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    _require_configured()
    model = payload.get("model") or ""
    request = _to_ollama_request(payload)
    request["stream"] = True
    chunk_id = f"chatcmpl-{uuid4().hex}"
    yield _sse_chunk(chunk_id, model, {"role": "assistant", "content": ""})
    try:
        stream = await _client().chat(**request)
        async for chunk in stream:
            message = getattr(chunk, "message", None)
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                for call in tool_calls:
                    function = call.get("function") if isinstance(call, dict) else None
                    if not function:
                        continue
                    arguments = function.get("arguments")
                    arguments = arguments if isinstance(arguments, dict) else {}
                    yield _sse_chunk(
                        chunk_id,
                        model,
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call_{uuid4().hex[:12]}",
                                    "type": "function",
                                    "function": {
                                        "name": function.get("name") or "",
                                        "arguments": json.dumps(arguments, ensure_ascii=False),
                                    },
                                }
                            ]
                        },
                    )
            content = getattr(message, "content", None)
            if content:
                yield _sse_chunk(chunk_id, model, {"content": content})
            if getattr(chunk, "done", False):
                prompt_tokens = int(getattr(chunk, "prompt_eval_count", 0) or 0)
                completion_tokens = int(getattr(chunk, "eval_count", 0) or 0)
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
                yield _sse_chunk(chunk_id, model, {}, "stop", usage)
    except ResponseError as exc:
        raise HTTPException(status_code=502, detail={"error": exc.error}) from exc
    yield b"data: [DONE]\n\n"


@llm_instrument("ol.chat.completions")
async def chat_completions(payload: dict[str, Any]) -> httpx.Response:
    _require_configured()
    try:
        response = await _client().chat(**_to_ollama_request(payload))
    except ResponseError as exc:
        raise HTTPException(status_code=502, detail={"error": exc.error}) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc
    data = _to_openai_response(payload.get("model") or "", response)
    return httpx.Response(status_code=200, json=data)


async def stream_chat_completions(payload: dict[str, Any]) -> AsyncIterator[bytes]:
    async for chunk in llm_byte_stream(
        "ol.chat.completions.stream",
        _stream_raw(payload),
        input_payload=payload,
    ):
        yield chunk
