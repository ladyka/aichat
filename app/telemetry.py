from __future__ import annotations

import logging
from collections.abc import Callable
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
        if endpoint.startswith("http://") or endpoint.rstrip("/").endswith(
            "/v1/traces"
        ):
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
