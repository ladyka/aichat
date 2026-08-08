from __future__ import annotations

import html
import secrets
from datetime import datetime, timedelta, timezone

import markdown
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_token
from app.config import get_settings
from app.db import Conversation, Message, ShareAccess, ShareLink, get_db
from app.routes.conversations import _require_user

router = APIRouter()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _share_key() -> tuple[str, str]:
    raw = "s_" + secrets.token_urlsafe(32)
    return raw, raw[:10]


def _share_payload(share: ShareLink, key: str) -> dict:
    return {
        "shared": True,
        "key": key,
        "created_at": _iso(share.created_at),
        "expires_at": _iso(share.expires_at),
    }


def _share_exists_payload(share: ShareLink) -> dict:
    return {
        "shared": True,
        "created_at": _iso(share.created_at),
        "expires_at": _iso(share.expires_at),
    }


def _active_share(db: Session, conversation_id: int) -> ShareLink | None:
    return db.scalar(
        select(ShareLink)
        .where(
            ShareLink.conversation_id == conversation_id,
            ShareLink.revoked_at.is_(None),
        )
        .order_by(ShareLink.created_at.desc())
        .limit(1)
    )


def _is_expired(share: ShareLink, now: datetime | None = None) -> bool:
    if share.expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    expires = share.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires < now


def _render_md(text: str) -> str:
    return markdown.markdown(
        html.escape(text),
        extensions=["fenced_code", "nl2br"],
    )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.client.host if request.client else "") or ""


@router.get("/api/conversations/{conversation_id}/share")
def get_share(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if not conversation:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    share = _active_share(db, conversation_id)
    if not share or _is_expired(share):
        return JSONResponse(status_code=404, content={"shared": False})
    return _share_exists_payload(share)


@router.post("/api/conversations/{conversation_id}/share")
def create_share(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if not conversation:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    existing = _active_share(db, conversation_id)
    if existing and not _is_expired(existing):
        return _share_exists_payload(existing)

    raw, prefix = _share_key()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.share_ttl_days)
    share = ShareLink(
        conversation_id=conversation_id,
        token_hash=hash_token(raw),
        prefix=prefix,
        expires_at=expires,
    )
    db.add(share)
    db.commit()
    return _share_payload(share, raw)


@router.post("/api/conversations/{conversation_id}/share/revoke")
def revoke_share(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if not conversation:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    now = datetime.now(timezone.utc)
    shares = db.scalars(
        select(ShareLink).where(
            ShareLink.conversation_id == conversation_id,
            ShareLink.revoked_at.is_(None),
        )
    ).all()
    for share in shares:
        share.revoked_at = now
    db.commit()
    return {"revoked": True}


@router.get("/s/{key}")
def share_page(key: str, request: Request, db: Session = Depends(get_db)):
    from app.routes.pages import render

    share = db.scalar(select(ShareLink).where(ShareLink.token_hash == hash_token(key)))
    if not share or share.revoked_at is not None or _is_expired(share):
        return render(
            request,
            "share.html",
            None,
            status_code=404,
            not_found=True,
        )

    conversation = db.scalar(select(Conversation).where(Conversation.id == share.conversation_id))
    if not conversation:
        return render(
            request,
            "share.html",
            None,
            status_code=404,
            not_found=True,
        )

    db.add(ShareAccess(share_id=share.id, ip=_client_ip(request)))
    db.commit()

    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    ).all()
    rendered = [{"role": m.role, "html": _render_md(m.content)} for m in messages]

    return render(
        request,
        "share.html",
        None,
        title=conversation.title,
        created_at=_iso(conversation.created_at),
        updated_at=_iso(conversation.updated_at),
        messages=rendered,
        not_found=False,
    )
