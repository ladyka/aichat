from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_user_session, hash_password
from app.config import get_settings
from app.db import OAuthIdentity, User, get_db, get_user_by_email, get_user_by_oauth
from app.oauth import (
    OAuthError,
    apple_authorize_url,
    consume_oauth_state,
    exchange_apple_code,
    exchange_github_code,
    exchange_google_code,
    exchange_vk_code,
    exchange_yandex_code,
    github_authorize_url,
    google_authorize_url,
    vk_authorize_url,
    yandex_authorize_url,
)
from app.routes.pages import render

router = APIRouter()


def _set_session_cookie(user: User, db: Session) -> RedirectResponse:
    settings = get_settings()
    raw = create_user_session(db, user)
    response = RedirectResponse("/chat", status_code=303)
    response.set_cookie(
        settings.session_cookie,
        raw,
        httponly=True,
        samesite="lax",
        max_age=settings.session_days * 86400,
    )
    return response


def _upsert_oauth_user(
    db: Session,
    provider: str,
    subject: str,
    email: str | None,
) -> User:
    user = get_user_by_oauth(db, provider, subject)
    if user:
        return user

    email_norm = (email or "").lower().strip()
    if email_norm:
        user = get_user_by_email(db, email_norm)
        if user:
            already_linked = db.scalar(
                select(OAuthIdentity).where(
                    OAuthIdentity.user_id == user.id,
                    OAuthIdentity.provider == provider,
                )
            )
            if already_linked:
                raise OAuthError("Этот аккаунт уже связан с другим профилем")
        else:
            user = User(
                email=email_norm,
                # OAuth users have no password; a random hash keeps password login unusable.
                password_hash=hash_password(secrets.token_urlsafe(32)),
            )
            db.add(user)
            db.flush()

    if not user:
        raise OAuthError("Не удалось определить аккаунт")

    db.add(OAuthIdentity(user_id=user.id, provider=provider, subject=subject))
    db.commit()
    return user


def _oauth_error_page(request: Request, message: str):
    return render(request, "login.html", None, status_code=400, error=message)


def _start_oauth(enabled: bool, authorize_url: str) -> RedirectResponse:
    if not enabled:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse(authorize_url, status_code=302)


async def _finish_code_login(
    request: Request,
    db: Session,
    provider: str,
    code: str,
    state: str,
    exchanger,
    *,
    error: str = "",
    error_description: str = "",
):
    if error:
        return _oauth_error_page(request, error_description or error)
    try:
        consume_oauth_state(state, provider)
        profile = await exchanger(code)
        user = _upsert_oauth_user(db, provider, profile["subject"], profile.get("email"))
    except OAuthError as exc:
        return _oauth_error_page(request, str(exc))
    return _set_session_cookie(user, db)


@router.get("/auth/google")
def google_login():
    return _start_oauth(get_settings().google_oauth_enabled, google_authorize_url())


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    db: Session = Depends(get_db),
):
    return await _finish_code_login(
        request,
        db,
        "google",
        code,
        state,
        exchange_google_code,
        error=error,
        error_description=error_description,
    )


@router.get("/auth/apple")
def apple_login():
    return _start_oauth(get_settings().apple_oauth_enabled, apple_authorize_url())


@router.post("/auth/apple/callback")
async def apple_callback(
    request: Request,
    code: str = Form(""),
    state: str = Form(""),
    user: str = Form(""),
    error: str = Form(""),
    error_description: str = Form(""),
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_error_page(request, error_description or error)
    try:
        consume_oauth_state(state, "apple")
        profile = await exchange_apple_code(code)
        email = profile["email"]
        if not email:
            try:
                payload = json.loads(user) if user else {}
                email = (payload.get("email") or "").lower().strip()
            except ValueError:
                email = ""
        user_row = _upsert_oauth_user(db, "apple", profile["subject"], email)
    except OAuthError as exc:
        return _oauth_error_page(request, str(exc))
    return _set_session_cookie(user_row, db)


@router.get("/auth/yandex")
def yandex_login():
    return _start_oauth(get_settings().yandex_oauth_enabled, yandex_authorize_url())


@router.get("/auth/yandex/callback")
async def yandex_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    db: Session = Depends(get_db),
):
    return await _finish_code_login(
        request,
        db,
        "yandex",
        code,
        state,
        exchange_yandex_code,
        error=error,
        error_description=error_description,
    )


@router.get("/auth/vk")
def vk_login():
    return _start_oauth(get_settings().vk_oauth_enabled, vk_authorize_url())


@router.get("/auth/vk/callback")
async def vk_callback(
    request: Request,
    code: str = "",
    state: str = "",
    device_id: str = "",
    error: str = "",
    error_description: str = "",
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_error_page(request, error_description or error)
    try:
        extra = consume_oauth_state(state, "vk")
        profile = await exchange_vk_code(
            code,
            device_id=device_id,
            code_verifier=extra.get("code_verifier", ""),
            state=state,
        )
        user = _upsert_oauth_user(db, "vk", profile["subject"], profile.get("email"))
    except OAuthError as exc:
        return _oauth_error_page(request, str(exc))
    return _set_session_cookie(user, db)


@router.get("/auth/github")
def github_login():
    return _start_oauth(get_settings().github_oauth_enabled, github_authorize_url())


@router.get("/auth/github/callback")
async def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    db: Session = Depends(get_db),
):
    return await _finish_code_login(
        request,
        db,
        "github",
        code,
        state,
        exchange_github_code,
        error=error,
        error_description=error_description,
    )
