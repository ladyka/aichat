from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.newrelic_telemetry import shutdown_newrelic, wrap_asgi
from app.routes import api_router
from app.telemetry import setup_telemetry, shutdown_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_telemetry()
    init_db()
    try:
        yield
    finally:
        shutdown_telemetry()
        shutdown_newrelic()


class _ASGI24ScopeMiddleware:
    """Keep response-body streaming in the request task (ASGI spec 2.4).

    uvicorn 0.52 reports asgi.spec_version "2.3", so Starlette streams response
    bodies via an anyio task group, running the body iterator in a separate
    asyncio task with its own contextvars context. Our OpenInference-instrumented
    stream generators attach the OpenTelemetry context token on their first
    advance (inside the request task) and detach it on exhaustion (inside the
    task-group task), which raises
    "ValueError: <Token> was created in a different Context". Claiming spec 2.4
    makes Starlette stream inline in the request task instead.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("asgi", {})["spec_version"] = "2.4"
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="aichat", lifespan=lifespan)
    app.add_middleware(_ASGI24ScopeMiddleware)
    app.include_router(api_router)
    static_dir = settings.root / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    chat_ui_dir = settings.root / "frontend" / "dist"
    if chat_ui_dir.is_dir():
        app.mount(
            "/chat-ui",
            StaticFiles(directory=str(chat_ui_dir)),
            name="chat-ui",
        )
    return app


app = wrap_asgi(create_app())
