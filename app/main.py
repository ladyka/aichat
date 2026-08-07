from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.routes import api_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    app = FastAPI(title="aichat", lifespan=lifespan)
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


app = create_app()
