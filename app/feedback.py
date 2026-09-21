"""Обратная связь из чата: жалоба, идея, вопрос команде через входящий вебхук.

Адрес задаётся ``FEEDBACK_WEBHOOK_URL``: Incoming Webhook Slack, Discord или
любой POST, который принимает JSON с полем ``text`` (у Discord — ``content``).
Сообщения в базу не пишутся — хранилище команды это канал вебхука.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any

import httpx2 as httpx

from app.config import get_settings
from app.notes import owned_conversation, parse_conversation_id

logger = logging.getLogger("aichat.feedback")

CATEGORIES = frozenset({"complaint", "idea", "question", "other"})
CATEGORY_LABELS = {
    "complaint": "Жалоба",
    "idea": "Идея",
    "question": "Вопрос",
    "other": "Обратная связь",
}
MAX_MESSAGE = 2000
MAX_OBJECTION = 2000
_DISCORD_CONTENT_MAX = 2000
_COMPLAINT_NEEDS_OBJECTION = (
    "Для жалобы нужно поле objection: что именно не понравилось "
    "(конкретный ответ, рука, ошибка, ожидание). Сначала уточни у пользователя."
)
_PREVIEW_HINT = (
    "Покажи пользователю краткое содержание и спроси, отправлять ли команде. "
    "Если да — вызови снова с теми же полями и confirm=true. "
    "Команда увидит email аккаунта."
)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _clip(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _is_discord(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "discord.com" or host.endswith(".discord.com") or host.endswith("discordapp.com")


def _webhook_body(url: str, text: str) -> dict[str, str]:
    if _is_discord(url):
        return {"content": text[:_DISCORD_CONTENT_MAX]}
    return {"text": text}


def format_feedback_text(
    *,
    category: str,
    message: str,
    objection: str,
    user_email: str,
    user_id: Any,
    conversation_id: str,
    conversation_title: str,
) -> str:
    label = CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])
    lines = [f"{label} от {user_email} (id {user_id})"]
    if conversation_id:
        title = f" «{conversation_title}»" if conversation_title else ""
        lines.append(f"Чат {conversation_id}{title}")
    lines.extend(["", "Сообщение:", message])
    if objection:
        lines.extend(["", "Что не понравилось:", objection])
    return "\n".join(lines)


async def post_feedback_webhook(url: str, text: str) -> str | None:
    """Отправить текст на вебхук. None — успех, иначе сообщение об ошибке."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "Адрес обратной связи задан неверно."
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=_webhook_body(url, text))
    except httpx.HTTPError:
        logger.warning("feedback webhook request failed")
        return "Не удалось отправить сообщение команде."
    if response.status_code >= 300:
        logger.warning("feedback webhook http=%s", response.status_code)
        return "Не удалось отправить сообщение команде."
    return None


def _conversation_meta(
    db: Any,
    user: Any,
    conversation_id: Any,
) -> tuple[str, str]:
    conv_id = parse_conversation_id(conversation_id)
    if conv_id is None or user is None or db is None:
        return "", ""
    conv = owned_conversation(db, user, conv_id)
    if conv is None:
        return str(conv_id), ""
    return str(conv.id), (conv.title or "").strip()


async def send_feedback(
    args: dict[str, Any],
    *,
    user: Any = None,
    db: Any = None,
    conversation_id: Any = None,
) -> str:
    settings = get_settings()
    if not settings.feedback_webhook_url:
        return _json({"error": "Обратная связь не настроена."})
    if user is None:
        return _json({"error": "Нет контекста пользователя."})

    category = str(args.get("category") or "").strip().lower()
    if category not in CATEGORIES:
        allowed = ", ".join(sorted(CATEGORIES))
        return _json({"error": f"Недопустимый category. Используй одно из: {allowed}."})

    message = _clip(str(args.get("message") or "").strip(), MAX_MESSAGE)
    if not message:
        return _json({"error": "Нужен параметр message — что передать команде."})
    objection = _clip(str(args.get("objection") or "").strip(), MAX_OBJECTION)
    confirm = bool(args.get("confirm"))

    if category == "complaint" and not objection:
        return _json({"error": _COMPLAINT_NEEDS_OBJECTION, "needs_objection": True})

    conv_id, conv_title = _conversation_meta(db, user, conversation_id)
    email = str(getattr(user, "email", "") or "")
    user_id = getattr(user, "id", None)
    preview = {
        "preview": True,
        "category": category,
        "message": message,
        "objection": objection,
        "user_email": email,
        "conversation_id": conv_id,
        "hint": _PREVIEW_HINT,
    }
    if not confirm:
        return _json(preview)

    text = format_feedback_text(
        category=category,
        message=message,
        objection=objection,
        user_email=email,
        user_id=user_id,
        conversation_id=conv_id,
        conversation_title=conv_title,
    )
    error = await post_feedback_webhook(settings.feedback_webhook_url, text)
    if error:
        return _json({"error": error})
    logger.info(
        "feedback sent user_id=%s category=%s conversation_id=%s",
        user_id,
        category,
        conv_id or "-",
    )
    return _json(
        {
            "ok": True,
            "category": category,
            "message": "Сообщение отправлено команде.",
        }
    )
