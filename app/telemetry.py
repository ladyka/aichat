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


def tool_output(span: Any, value: str) -> None:
    """Attach the tool result to a TOOL span opened by :func:`tool_span`."""
    if span is None:
        return
    from openinference.semconv.trace import SpanAttributes

    span.set_attribute(SpanAttributes.OUTPUT_VALUE, value)
    span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, "text/plain")


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
    """
    if tracer is None:
        async for chunk in chunks:
            yield chunk
        return
    from openinference.instrumentation import using_session

    with using_session(session_id):
        async for chunk in chunks:
            yield chunk
