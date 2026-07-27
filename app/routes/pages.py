from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    create_api_token_raw,
    create_user_session,
    delete_session,
    get_current_user_optional,
    get_user_from_session,
    hash_password,
    hash_token,
    verify_password,
)
from app.config import get_settings
from app.db import ApiToken, User, get_db, get_user_by_email

router = APIRouter()
templates = Jinja2Templates(directory=str(get_settings().root / "templates"))


def _ctx(user: User | None = None, **extra):
    settings = get_settings()
    data = {
        "user": user,
        "app_name": "aichat",
        "default_model": settings.default_model,
    }
    data.update(extra)
    return data


def render(request: Request, name: str, user: User | None = None, status_code: int = 200, **extra):
    return templates.TemplateResponse(
        request,
        name,
        _ctx(user, **extra),
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, user: User | None = Depends(get_current_user_optional)):
    return render(request, "landing.html", user)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if user:
        return RedirectResponse("/chat", status_code=303)
    return render(request, "login.html", user, error=None)


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    found = get_user_by_email(db, email)
    if not found or not verify_password(password, found.password_hash):
        return render(
            request,
            "login.html",
            None,
            status_code=400,
            error="Неверный email или пароль",
        )
    raw = create_user_session(db, found)
    response = RedirectResponse("/chat", status_code=303)
    response.set_cookie(
        settings.session_cookie,
        raw,
        httponly=True,
        samesite="lax",
        max_age=settings.session_days * 86400,
    )
    return response


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if user:
        return RedirectResponse("/chat", status_code=303)
    return render(request, "register.html", user, error=None)


@router.post("/register")
def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    email_norm = email.lower().strip()
    error = None
    if "@" not in email_norm or "." not in email_norm:
        error = "Укажите корректный email"
    elif len(password) < 6:
        error = "Пароль должен быть не короче 6 символов"
    elif password != password2:
        error = "Пароли не совпадают"
    elif get_user_by_email(db, email_norm):
        error = "Пользователь с таким email уже есть"

    if error:
        return render(request, "register.html", None, status_code=400, error=error)

    user = User(email=email_norm, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
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


@router.post("/logout")
@router.get("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie)
    delete_session(db, raw)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(settings.session_cookie)
    return response


@router.get("/chat", response_class=HTMLResponse)
def chat_page(
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "chat.html", user)


@router.get("/tokens", response_class=HTMLResponse)
def tokens_page(
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login", status_code=303)
    tokens = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    ).all()
    return render(request, "tokens.html", user, tokens=tokens, new_token=None)


@router.post("/tokens")
def create_token(
    request: Request,
    name: str = Form("default"),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login", status_code=303)

    raw, prefix = create_api_token_raw()
    token = ApiToken(
        user_id=user.id,
        name=(name or "default").strip()[:100],
        token_hash=hash_token(raw),
        prefix=prefix,
    )
    db.add(token)
    db.commit()

    tokens = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    ).all()
    return render(request, "tokens.html", user, tokens=tokens, new_token=raw)


@router.post("/tokens/{token_id}/revoke")
def revoke_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login", status_code=303)

    token = db.get(ApiToken, token_id)
    if token and token.user_id == user.id and token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return RedirectResponse("/tokens", status_code=303)
