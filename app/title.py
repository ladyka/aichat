from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.db import Conversation, Message, SessionLocal
from app.models_catalog import PROVIDER_E7_BY, PROVIDER_OL, ModelRoute, resolve_model

logger = logging.getLogger("aichat.title")

# После N-го ответа ассистента тема диалога переопределяется заново.
TITLE_CHECKPOINTS = (1, 2, 5)
TRANSCRIPT_MAX_MESSAGES = 12
TRANSCRIPT_MAX_CHARS = 400
TITLE_MAX_CHARS = 80
# Заголовок просим в 6 слов; всё, что длиннее, — скорее всего рассуждения модели.
TITLE_MAX_WORDS = 12
# С запасом: некоторые thinking-модели тратят часть бюджета на рассуждения.
TITLE_MAX_TOKENS = 512
# glm-5.3-flash игнорирует think=false и пишет рассуждения прямо в content,
# отделяя финальный ответ закрывающим тегом рассуждений.
THINK_MARKER = "</think>"

TITLE_SYSTEM_PROMPT = (
    "Ты формулируешь название темы диалога по его сообщениям. Придумай короткое "
    "название: до 6 слов, на языке диалога (по умолчанию — русский), без кавычек "
    "и точки в конце. Ответь только названием, без пояснений."
)


def _build_transcript(messages: list[Message]) -> str:
    parts: list[str] = []
    for message in messages:
        content = (message.content or "").strip()
        if not content:
            continue
        if len(content) > TRANSCRIPT_MAX_CHARS:
            content = content[:TRANSCRIPT_MAX_CHARS] + "…"
        parts.append(f"{message.role}: {content}")
        if len(parts) >= TRANSCRIPT_MAX_MESSAGES:
            break
    return "\n".join(parts)


def _truncate_title(raw: str) -> str:
    text = raw or ""
    if THINK_MARKER in text:
        text = text.rsplit(THINK_MARKER, 1)[-1]
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    # Thinking-модель, проигнорировавшая think=false, дописывает рассуждения перед
    # ответом: заголовок всегда идёт последней строкой.
    title = lines[-1].strip("\"'«»“”").rstrip(".").strip()
    if len(title.split()) > TITLE_MAX_WORDS:
        # Незавершённые рассуждения, а не заголовок — лучше оставить старое название.
        return ""
    return title[:TITLE_MAX_CHARS]


async def _call_llm(route: ModelRoute, payload: dict[str, Any]) -> Any:
    from app.model_providers import e7by, ol
    from app.model_providers.openrouter import chat_completions

    if route.provider == PROVIDER_E7_BY:
        return await e7by.chat_completions(payload)
    if route.provider == PROVIDER_OL:
        return await ol.chat_completions(payload)
    return await chat_completions(payload)


async def _request_title(transcript: str) -> str:
    settings = get_settings()
    try:
        route = resolve_model(settings.title_model)
    except HTTPException as exc:
        # TITLE_MODEL недоступна (нет в каталоге/провайдер выключен) — фолбэк на дефолтную.
        logger.warning(
            "TITLE_MODEL %r unavailable (%s), using default", settings.title_model, exc.detail
        )
        route = resolve_model(None)
    payload: dict[str, Any] = {
        "model": route.upstream_id,
        "messages": [
            {"role": "system", "content": TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "max_tokens": TITLE_MAX_TOKENS,
        "temperature": 0.3,
    }
    if route.provider == PROVIDER_OL:
        # Без этого thinking-модель тратит весь num_predict на рассуждения,
        # возвращает пустой content — и тема не проставляется.
        payload["think"] = False
    response = await _call_llm(route, payload)
    if response.status_code >= 400:
        raise RuntimeError(f"title model error: {response.status_code}")
    data = response.json()
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return _truncate_title(content)


async def update_conversation_title(conversation_id: int) -> None:
    """Фоновая задача: сформулировать тему диалога и обновить title.

    Запускается после сохранения ответа ассистента, когда их в диалоге 1, 2 или 5
    (см. TITLE_CHECKPOINTS). Ошибки только логируются — чат не должен падать.
    """
    db = SessionLocal()
    try:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.title_locked:
            return
        messages = db.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        ).all()
        assistant_count = sum(1 for m in messages if m.role == "assistant")
        if assistant_count not in TITLE_CHECKPOINTS:
            return
        transcript = _build_transcript(messages)
        if not transcript:
            return
        try:
            title = await _request_title(transcript)
        except Exception as exc:
            logger.warning("title generation failed for conversation %s: %s", conversation_id, exc)
            return
        if not title:
            logger.warning(
                "title generation returned an empty title for conversation %s", conversation_id
            )
            return
        conversation.title = title
        conversation.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "conversation %s title set to %r (assistant_count=%s)",
            conversation_id,
            title,
            assistant_count,
        )
    finally:
        db.close()
