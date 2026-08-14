from __future__ import annotations

import re

from fastapi import Request

from app.config import get_settings

OG_IMAGE_PATH = "/static/og/share.png"
OG_IMAGE_WIDTH = 1200
OG_IMAGE_HEIGHT = 630
DEFAULT_OG_DESCRIPTION = "Общайтесь с ИИ в приватном чате"
SNIPPET_LIMIT = 180


def public_origin(request: Request) -> str:
    settings = get_settings()
    if settings.public_base_url:
        return settings.public_base_url
    return str(request.base_url).rstrip("/")


def canonical_url(request: Request) -> str:
    return public_origin(request) + request.url.path


def og_image_url(request: Request) -> str:
    return public_origin(request) + OG_IMAGE_PATH


def og_context(request: Request, **overrides) -> dict:
    data = {
        "og_description": DEFAULT_OG_DESCRIPTION,
        "og_url": canonical_url(request),
        "og_image": og_image_url(request),
        "og_image_width": OG_IMAGE_WIDTH,
        "og_image_height": OG_IMAGE_HEIGHT,
        "og_type": "website",
        "og_site_name": "aichat",
        "robots": None,
    }
    data.update(overrides)
    return data


def plain_snippet(text: str, limit: int = SNIPPET_LIMIT) -> str:
    cleaned = re.sub(r"[#*_`>~\[\]()]", " ", text or "")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
