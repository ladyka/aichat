from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager, nullcontext
from typing import Any, TypeVar

from app.config import get_settings

logger = logging.getLogger("aichat.telemetry")

F = TypeVar("F", bound=Callable[..., Any])

tracer_provider: Any = None
tracer: Any = None


def setup_telemetry() -> Any:
    """Register Arize/Phoenix OTLP exporter when credentials are configured."""
    global tracer_provider, tracer

    if tracer_provider is not None:
        return tracer_provider

    settings = get_settings()
    if not settings.arize_enabled:
        logger.info("Arize/Phoenix tracing disabled (set ARIZE_SPACE_ID and ARIZE_API_KEY)")
        return None

    from arize.otel import Transport, register
    from openinference.instrumentation import OITracer, TraceConfig

    endpoint = settings.arize_otlp_endpoint
    transport = Transport.GRPC
    register_kwargs: dict[str, Any] = {
        "space_id": settings.arize_space_id,
        "api_key": settings.arize_api_key,
        "project_name": settings.arize_project_name,
        "verbose": False,
    }
    if endpoint:
        register_kwargs["endpoint"] = endpoint
        # gRPC for Arize AX cloud endpoints (https://…/v1), like example/aichat.
        # HTTP only for OTLP/HTTP collectors: local Phoenix and https /v1/traces.
        if endpoint.startswith("http://") or endpoint.rstrip("/").endswith("/v1/traces"):
            transport = Transport.HTTP
        register_kwargs["transport"] = transport

    try:
        tracer_provider = register(**register_kwargs)
        tracer = OITracer(tracer_provider.get_tracer("aichat"), config=TraceConfig())
    except Exception:
        logger.exception("Failed to initialize Arize/Phoenix tracing")
        tracer_provider = None
        tracer = None
        return None

    logger.info(
        "Arize/Phoenix tracing enabled project=%s endpoint=%s transport=%s",
        settings.arize_project_name,
        endpoint or "default(arize)",
        transport.value if endpoint else "grpc",
    )
    return tracer_provider


def shutdown_telemetry() -> None:
    global tracer_provider, tracer
    if tracer_provider is None:
        return
    try:
        tracer_provider.force_flush()
        tracer_provider.shutdown()
    except Exception:
        logger.exception("Failed to shut down tracer provider")
    tracer_provider = None
    tracer = None


async def llm_byte_stream(
    name: str,
    chunks: AsyncIterator[bytes],
    *,
    input_payload: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    """Yield SSE/HTTP chunks under an LLM span that is not attached as current.

    ``start_as_current_span`` / ``@tracer.llm`` on an async generator attach a
    ContextVar token on first ``__anext__`` and detach it when the generator
    closes. Chat peeks that generator inside ``chain_span``, then Starlette
    finishes it after the span manager has exited — OpenTelemetry then logs
    ``Failed to detach context``. ``start_span`` + ``span.end()`` records the
    same LLM span without touching the context stack. Input/output and status
    OK are set explicitly so Phoenix does not show an empty UNSET span.
    """
    if tracer is None:
        async for chunk in chunks:
            yield chunk
        return
    from openinference.instrumentation._attributes import (
        get_input_attributes,
        get_llm_attributes,
    )
    from openinference.semconv.trace import OpenInferenceSpanKindValues
    from opentelemetry.trace import Status, StatusCode

    attributes: dict[str, Any] = {}
    if input_payload is not None:
        traced = payload_for_trace(input_payload)
        attributes.update(get_input_attributes(traced))
        attributes.update(
            get_llm_attributes(
                model_name=str(traced.get("model") or "") or None,
                input_messages=(
                    traced.get("messages") if isinstance(traced.get("messages"), list) else None
                ),
            )
        )
    span = tracer.start_span(
        name,
        openinference_span_kind=OpenInferenceSpanKindValues.LLM,
        attributes=attributes or None,
    )
    collected = bytearray()
    try:
        async for chunk in chunks:
            collected.extend(chunk)
            yield chunk
    except Exception as exc:
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        raise
    else:
        _record_llm_stream_output(span, bytes(collected))
        span.set_status(Status(StatusCode.OK))
    finally:
        close = getattr(chunks, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logger.debug("llm stream aclose failed", exc_info=True)
        span.end()


def _sse_output_text(raw: bytes) -> str:
    """Collect assistant text from an OpenAI-style SSE body."""
    texts: list[str] = []
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
            texts.append(content)
    return "".join(texts)


def _sse_tool_calls(raw: bytes) -> list[dict[str, str]]:
    """Assemble streamed tool_calls the same way as app.tools.extract_tool_calls."""
    calls: dict[int, dict[str, str]] = {}
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
        deltas = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("tool_calls")
        if not deltas:
            continue
        for delta in deltas:
            if not isinstance(delta, dict):
                continue
            index = int(delta.get("index", 0))
            entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if delta.get("id"):
                entry["id"] = str(delta["id"])
            fn = delta.get("function") or {}
            if fn.get("name"):
                entry["name"] += str(fn["name"])
            if fn.get("arguments"):
                entry["arguments"] += str(fn["arguments"])
    return [calls[index] for index in sorted(calls)]


def _record_llm_stream_output(span: Any, raw: bytes) -> None:
    """Write assistant text and/or tool_calls onto an LLM span for Phoenix."""
    from openinference.instrumentation._attributes import get_llm_attributes

    text = _sse_output_text(raw)
    calls = _sse_tool_calls(raw)
    if calls:
        rendered = json.dumps(
            [{"name": call["name"], "arguments": call["arguments"]} for call in calls],
            ensure_ascii=False,
        )
        span.set_output(value=f"{text}\n{rendered}".strip() if text else rendered)
        span.set_attributes(
            get_llm_attributes(
                output_messages=[
                    {
                        "role": "assistant",
                        "content": text or None,
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "function": {
                                    "name": call["name"],
                                    "arguments": call["arguments"],
                                },
                            }
                            for call in calls
                        ],
                    }
                ]
            )
        )
        return
    if text:
        span.set_output(value=text)


_TRACE_JSON_MAX = 8000


def payload_for_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy a chat payload, compacting tool results so OTLP attributes stay small."""
    messages = []
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        item: dict[str, Any] = {}
        for key in ("role", "content", "tool_call_id", "tool_calls"):
            if key in message:
                item[key] = message[key]
        if item.get("role") == "tool":
            item["content"] = compact_tool_content(str(item.get("content") or ""))
        messages.append(item)
    return {"model": payload.get("model"), "messages": messages}


def compact_tool_content(content: str) -> str:
    """Keep pzz.by menu traces readable: titles + count, not photo URLs."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return content[:_TRACE_JSON_MAX]
    if not isinstance(data, dict):
        dumped = json.dumps(data, ensure_ascii=False)
        return dumped[:_TRACE_JSON_MAX]
    items = data.get("items")
    documents = data.get("documents")
    if data.get("source") == "pravo.by" and isinstance(documents, list):
        titles = [
            str(item.get("title"))
            for item in documents
            if isinstance(item, dict) and item.get("title")
        ]
        compact = {
            "source": data.get("source"),
            "query": data.get("query"),
            "registry_number": data.get("registry_number"),
            "count": data.get("count", len(titles)),
            "titles": titles,
        }
        if data.get("error"):
            compact["error"] = data["error"]
        return json.dumps(compact, ensure_ascii=False)
    if data.get("source") == "pravo.by":
        compact = {
            "source": data.get("source"),
            "kind": data.get("kind"),
            "title": data.get("title"),
            "url": data.get("url"),
            "registry_number": data.get("registry_number"),
        }
        if data.get("error"):
            compact["error"] = data["error"]
        return json.dumps(compact, ensure_ascii=False)
    if data.get("source") == "pzz.by" and isinstance(items, list):
        titles = [
            str(item.get("title")) for item in items if isinstance(item, dict) and item.get("title")
        ]
        compact = {
            "source": data.get("source"),
            "query": data.get("query"),
            "count": data.get("count", len(titles)),
            "titles": titles,
        }
        if data.get("error"):
            compact["error"] = data["error"]
        if data.get("order_num"):
            compact["order_num"] = data["order_num"]
            compact["submitted"] = data.get("submitted")
        return json.dumps(compact, ensure_ascii=False)
    dumped = json.dumps(data, ensure_ascii=False)
    return dumped[:_TRACE_JSON_MAX]


def llm_instrument(name: str) -> Callable[[F], F]:
    """Apply OITracer.llm when tracing is enabled; otherwise leave fn unchanged."""

    def decorator(fn: F) -> F:
        setup_telemetry()
        if tracer is None:
            return fn
        return tracer.llm(name=name)(fn)  # type: ignore[no-any-return]

    return decorator


def chain_instrument(name: str) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        setup_telemetry()
        if tracer is None:
            return fn
        return tracer.chain(name=name)(fn)  # type: ignore[no-any-return]

    return decorator


@contextmanager
def chain_span(name: str) -> Iterator[Any]:
    """Open an OpenInference CHAIN span bound to the current context (or no-op).

    Yields the span, or ``None`` when tracing is disabled. Child spans created
    while it is active (llm/tool spans) are nested under it.
    """
    if tracer is None:
        yield None
        return
    from openinference.semconv.trace import OpenInferenceSpanKindValues

    with tracer.start_as_current_span(
        name,
        openinference_span_kind=OpenInferenceSpanKindValues.CHAIN,
    ) as span:
        yield span
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.OK))


@contextmanager
def tool_span(name: str, arguments: str) -> Iterator[Any]:
    """Open an OpenInference TOOL span for one tool call (or no-op).

    Yields the span, or ``None`` when tracing is disabled. Records the tool
    name and its JSON arguments as span attributes; the caller reports the
    result via :func:`tool_output`.
    """
    if tracer is None:
        yield None
        return
    from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes

    attributes = {
        SpanAttributes.TOOL_NAME: name,
        SpanAttributes.INPUT_MIME_TYPE: "application/json",
        SpanAttributes.INPUT_VALUE: json.dumps(
            {"name": name, "arguments": arguments},
            ensure_ascii=False,
        ),
    }
    with tracer.start_as_current_span(
        name,
        openinference_span_kind=OpenInferenceSpanKindValues.TOOL,
        attributes=attributes,
    ) as span:
        yield span
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.OK))


def tool_output(span: Any, value: str) -> None:
    """Attach the tool result to a TOOL span opened by :func:`tool_span`."""
    if span is None:
        return
    from openinference.semconv.trace import SpanAttributes

    span.set_attribute(SpanAttributes.OUTPUT_VALUE, compact_tool_content(value))
    span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, "application/json")


def request_context(user_id: str, session_id: str) -> Any:
    """OpenInference context that stamps ``user.id`` and ``session.id`` onto spans.

    Open it at the request boundary: every span created while it is active
    (llm/tool/chain) carries the attributes, which groups all turns of one
    conversation into a single session in Arize AX. No-op when tracing is off.
    """
    if tracer is None:
        return nullcontext()
    from openinference.instrumentation import using_attributes

    return using_attributes(user_id=user_id, session_id=session_id)


async def stream_in_session(
    session_id: str,
    chunks: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Iterate an SSE byte stream with ``session.id`` active in the context.

    Async spans are created when a generator is first advanced, which happens
    after the request handler returns (during response streaming). Keeping the
    session context across the stream ensures those spans carry ``session.id``.

    The inner iterator is closed *before* ``using_session`` exits so nested
    span tokens detach in LIFO order. ``ValueError`` from a token created in
    another asyncio task (uvicorn/New Relic body streaming) is swallowed —
    the request already finished successfully.
    """
    if tracer is None:
        async for chunk in chunks:
            yield chunk
        return
    from openinference.instrumentation import using_session

    cm = using_session(session_id)
    cm.__enter__()
    try:
        async for chunk in chunks:
            yield chunk
    finally:
        close = getattr(chunks, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logger.debug("stream_in_session aclose failed", exc_info=True)
        try:
            cm.__exit__(None, None, None)
        except ValueError:
            logger.debug(
                "opentelemetry context detach skipped (token from another task)",
                exc_info=True,
            )
