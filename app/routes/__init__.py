from __future__ import annotations

from fastapi import APIRouter

from app.routes import api, conversations, notes, oauth, pages, share

api_router = APIRouter()
api_router.include_router(pages.router)
api_router.include_router(oauth.router)
api_router.include_router(api.router)
api_router.include_router(conversations.router)
api_router.include_router(notes.router)
api_router.include_router(share.router)
