from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import ApiToken, User, UserSession, get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


def create_api_token_raw() -> tuple[str, str]:
    raw = "aichat_" + secrets.token_urlsafe(32)
    prefix = raw[:12]
    return raw, prefix


def create_user_session(db: Session, user: User) -> str:
    settings = get_settings()
    raw = create_session_token()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_days)
    session = UserSession(user_id=user.id, token_hash=hash_token(raw), expires_at=expires)
    db.add(session)
    db.commit()
    return raw


def delete_session(db: Session, raw: str | None) -> None:
    if not raw:
        return
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw)))
    if session:
        db.delete(session)
        db.commit()


def get_user_from_session(db: Session, raw: str | None) -> User | None:
    if not raw:
        return None
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw)))
    if not session:
        return None
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None
    return db.get(User, session.user_id)


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie)
    return get_user_from_session(db, raw)


def get_user_from_api_token(db: Session, authorization: str | None) -> tuple[User, ApiToken]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    raw = authorization.split(" ", 1)[1].strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Invalid token")
    token = db.scalar(
        select(ApiToken).where(
            ApiToken.token_hash == hash_token(raw),
            ApiToken.revoked_at.is_(None),
        )
    )
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user, token
