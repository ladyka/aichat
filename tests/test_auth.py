from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.auth import (
    create_api_token_raw,
    create_session_token,
    create_user_session,
    delete_session,
    get_user_from_api_token,
    get_user_from_session,
    hash_password,
    hash_token,
    verify_password,
)
from app.db import User, UserSession

from tests.conftest import create_token as make_token
from tests.conftest import register


def test_password_hash_roundtrip():
    hashed = hash_password("hunter2")
    assert hashed != "hunter2"
    assert verify_password("hunter2", hashed)
    assert not verify_password("wrong", hashed)


def test_hash_token_is_sha256_hex():
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64
    assert hash_token("abc") != hash_token("abd")


def test_create_api_token_raw():
    raw, prefix = create_api_token_raw()
    assert raw.startswith("aichat_")
    assert raw[:12] == prefix
    assert len(raw) > 30


def test_create_session_token_unique():
    assert create_session_token() != create_session_token()


def test_create_and_get_session(client, db):
    email = "session@example.com"
    register(client, email)
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None

    raw = create_user_session(db, user)
    assert get_user_from_session(db, raw) is not None
    assert get_user_from_session(db, "bogus") is None
    assert get_user_from_session(db, None) is None


def test_expired_session_is_deleted(client, db):
    email = "expired@example.com"
    register(client, email)
    user = db.scalar(select(User).where(User.email == email))
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token("expiredraw"),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db.add(session)
    db.commit()

    assert get_user_from_session(db, "expiredraw") is None
    assert db.scalar(select(UserSession).where(UserSession.id == session.id)) is None


def test_delete_session(client, db):
    email = "delete@example.com"
    register(client, email)
    user = db.scalar(select(User).where(User.email == email))
    raw = create_user_session(db, user)
    delete_session(db, raw)
    assert get_user_from_session(db, raw) is None
    delete_session(db, None)


def test_api_token_auth(client, db):
    email = "api-auth@example.com"
    register(client, email)
    raw, _ = make_token(client)
    user_row = db.scalar(select(User).where(User.email == email))
    assert user_row is not None

    user, found = get_user_from_api_token(db, f"Bearer {raw}")
    assert user.email == email
    assert found is not None

    with pytest.raises(HTTPException):
        get_user_from_api_token(db, None)
    with pytest.raises(HTTPException):
        get_user_from_api_token(db, "Basic abc")
    with pytest.raises(HTTPException):
        get_user_from_api_token(db, "Bearer bogus")
