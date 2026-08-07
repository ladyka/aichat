from __future__ import annotations

from fastapi import APIRouter

from app.routes import api, conversations, pages

api_router = APIRouter()
api_router.include_router(pages.router)
api_router.include_router(api.router)
api_router.include_router(conversations.router)
