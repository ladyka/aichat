from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import get_user_from_session
from app.config import get_settings
from app.db import Conversation, Message, User, get_db
from app.models_catalog import PUBLIC_DEFAULT_ID, get_models_list, resolve_upstream_model
from app.skills import (
    conversation_skill_ids,
    copy_defaults_to_conversation,
    default_skill_ids,
    set_conversation_skills,
    set_default_skill_ids,
)
from app.title import TITLE_CHECKPOINTS, update_conversation_title

router = APIRouter()


def _require_user(request: Request, db: Session) -> User | JSONResponse:
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return user


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _conversation_summary(c: Conversation) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "title": c.title,
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
        "archived_at": _iso(c.archived_at),
        "skill_ids": conversation_skill_ids(c),
    }


def _message_item(m: Message) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "created_at": _iso(m.created_at),
    }


def _get_owned_conversation(db: Session, user: User, conversation_id: int) -> Conversation | None:
    return db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .options(selectinload(Conversation.messages), selectinload(Conversation.skill_links))
    )


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None


class MessageCreate(BaseModel):
    role: str
    content: str


class MessagesAppend(BaseModel):
    messages: list[MessageCreate] = Field(default_factory=list)


class SettingsUpdate(BaseModel):
    preferred_model: str | None = None
    default_skill_ids: list[str] | None = None


class ConversationSkillsUpdate(BaseModel):
    skill_ids: list[str] = Field(default_factory=list)


@router.get("/api/conversations")
def list_conversations(request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.archived_at.is_(None))
        .options(selectinload(Conversation.skill_links))
        .order_by(Conversation.updated_at.desc())
    ).all()
    return {"data": [_conversation_summary(c) for c in rows]}


@router.post("/api/conversations")
def create_conversation(
    request: Request,
    body: ConversationCreate,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    title = (body.title or "Новый чат").strip()[:200] or "Новый чат"
    now = datetime.now(timezone.utc)
    conv = Conversation(user_id=user.id, title=title, created_at=now, updated_at=now)
    db.add(conv)
    db.flush()
    copy_defaults_to_conversation(db, user, conv)
    db.commit()
    db.refresh(conv)
    db.refresh(conv, attribute_names=["skill_links"])
    return _conversation_summary(conv)


@router.get("/api/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conv = _get_owned_conversation(db, user, conversation_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return {
        **_conversation_summary(conv),
        "messages": [_message_item(m) for m in conv.messages],
    }


@router.patch("/api/conversations/{conversation_id}")
def patch_conversation(
    conversation_id: int,
    request: Request,
    body: ConversationPatch,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conv = _get_owned_conversation(db, user, conversation_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if body.title is not None:
        new_title = body.title.strip()[:200]
        if new_title and new_title != conv.title:
            # Ручное переименование: авто-тема больше не перезаписывает название.
            conv.title = new_title
            conv.title_locked = True
    if body.archived is True:
        conv.archived_at = datetime.now(timezone.utc)
    elif body.archived is False:
        conv.archived_at = None
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conv)
    return _conversation_summary(conv)


@router.put("/api/conversations/{conversation_id}/skills")
def put_conversation_skills(
    conversation_id: int,
    request: Request,
    body: ConversationSkillsUpdate,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conv = _get_owned_conversation(db, user, conversation_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    skill_ids, err = set_conversation_skills(db, user, conv, body.skill_ids)
    if err:
        return JSONResponse(status_code=400, content={"error": err})
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conv)
    db.refresh(conv, attribute_names=["skill_links"])
    return {"skill_ids": skill_ids}


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conv = _get_owned_conversation(db, user, conversation_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.post("/api/conversations/{conversation_id}/messages")
def append_messages(
    conversation_id: int,
    request: Request,
    body: MessagesAppend,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conv = _get_owned_conversation(db, user, conversation_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    created: list[Message] = []
    for item in body.messages:
        role = (item.role or "").strip().lower()
        if role not in ("user", "assistant", "system"):
            return JSONResponse(status_code=400, content={"error": f"Invalid role: {item.role}"})
        content = item.content if item.content is not None else ""
        msg = Message(conversation_id=conv.id, role=role, content=content)
        db.add(msg)
        created.append(msg)

    if conv.title == "Новый чат":
        for item in body.messages:
            if item.role == "user" and item.content.strip():
                conv.title = item.content.strip().replace("\n", " ")[:80]
                break

    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    for msg in created:
        db.refresh(msg)

    # Фон: после 1-го, 2-го и 5-го ответа ассистента переформулировать тему.
    assistant_total = (
        db.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conv.id, Message.role == "assistant"
            )
        )
        or 0
    )
    if assistant_total in TITLE_CHECKPOINTS:
        background_tasks.add_task(update_conversation_title, conv.id)

    return {"data": [_message_item(m) for m in created]}


@router.get("/api/settings")
def get_settings_api(request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    model = (user.preferred_model or "").strip() or PUBLIC_DEFAULT_ID
    return {"preferred_model": model, "default_skill_ids": default_skill_ids(db, user)}


@router.put("/api/settings")
async def put_settings_api(
    request: Request,
    body: SettingsUpdate,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user

    if body.preferred_model is not None:
        model = (body.preferred_model or "").strip() or PUBLIC_DEFAULT_ID
        await get_models_list()
        try:
            resolve_upstream_model(model)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
        user.preferred_model = model

    if body.default_skill_ids is not None:
        _, err = set_default_skill_ids(db, user, body.default_skill_ids)
        if err:
            return JSONResponse(status_code=400, content={"error": err})

    db.commit()
    model = (user.preferred_model or "").strip() or PUBLIC_DEFAULT_ID
    return {"preferred_model": model, "default_skill_ids": default_skill_ids(db, user)}
