from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
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
from app.db import ApiToken, ApiTokenUsage, User, get_db, get_user_by_email
from app.models_catalog import PUBLIC_DEFAULT_ID, get_models_list, resolve_upstream_model
from app.og import og_context
from app.skills import default_skill_ids, list_owned_skills, set_default_skill_ids, skill_payload

router = APIRouter()
templates = Jinja2Templates(directory=str(get_settings().root / "templates"))


def safe_next_path(raw: str | None) -> str | None:
    """Allow only same-origin relative paths (for post-login return)."""
    if not raw:
        return None
    value = raw.strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    if "://" in value:
        return None
    return value


def _ctx(user: User | None = None, **extra):
    settings = get_settings()
    data = {
        "user": user,
        "app_name": "aichat",
        "default_model": settings.default_model,
        "google_oauth_enabled": settings.google_oauth_enabled,
        "apple_oauth_enabled": settings.apple_oauth_enabled,
        "yandex_oauth_enabled": settings.yandex_oauth_enabled,
        "vk_oauth_enabled": settings.vk_oauth_enabled,
        "github_oauth_enabled": settings.github_oauth_enabled,
        "any_oauth_enabled": settings.any_oauth_enabled,
    }
    data.update(extra)
    return data


def render(request: Request, name: str, user: User | None = None, status_code: int = 200, **extra):
    extra = {**og_context(request), **extra}
    return templates.TemplateResponse(
        request,
        name,
        _ctx(user, **extra),
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, user: User | None = Depends(get_current_user_optional)):
    return render(request, "landing.html", user)


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    return render(
        request,
        "about.html",
        user,
        og_description=(
            "Чат в браузере: пишете как знакомому. "
            "Модель отвечает, а если нужно — сходит за погодой, картинкой или пиццей."
        ),
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    return render(
        request,
        "privacy.html",
        user,
        og_description="Как aichat обрабатывает данные аккаунта, чатов и техническую информацию.",
    )


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    return render(
        request,
        "terms.html",
        user,
        og_description="Условия использования сервиса aichat: аккаунт, чат, API и ответственность.",
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    next_url: str | None = Query(default=None, alias="next"),
):
    dest = safe_next_path(next_url)
    if user:
        return RedirectResponse(dest or "/chat", status_code=303)
    return render(request, "login.html", user, error=None, next_path=dest)


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    dest = safe_next_path(next)
    found = get_user_by_email(db, email)
    if not found or not verify_password(password, found.password_hash):
        return render(
            request,
            "login.html",
            None,
            status_code=400,
            error="Неверный email или пароль",
            next_path=dest,
        )
    raw = create_user_session(db, found)
    response = RedirectResponse(dest or "/chat", status_code=303)
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
    consent: str = Form(""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    email_norm = email.lower().strip()
    error = None
    if "@" not in email_norm or "." not in email_norm:
        error = "Укажите корректный email"
    elif consent != "on":
        error = "Необходимо согласие с политикой конфиденциальности и пользовательским соглашением"
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


def _settings_render(
    request: Request,
    user: User,
    db: Session,
    *,
    error: str | None = None,
    saved: bool = False,
    token_error: str | None = None,
    new_token: str | None = None,
    status_code: int = 200,
):
    settings = get_settings()
    preferred = (user.preferred_model or "").strip() or settings.default_model or PUBLIC_DEFAULT_ID
    tokens, usage = _tokens_view_data(db, user)
    owned = list_owned_skills(db, user)
    return render(
        request,
        "settings.html",
        user,
        status_code=status_code,
        preferred_model=preferred,
        error=error,
        saved=saved,
        token_error=token_error,
        new_token=new_token,
        tokens=tokens,
        token_usage=usage,
        api_daily_limit=settings.api_daily_limit,
        max_tokens_per_user=settings.max_tokens_per_user,
        active_tokens=len(tokens),
        skill_items=[skill_payload(skill) for skill in owned],
        default_skill_ids=default_skill_ids(db, user),
    )


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "profile.html", user)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login", status_code=303)
    return _settings_render(request, user, db)


@router.post("/settings", response_class=HTMLResponse)
async def settings_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    preferred_model = str(form.get("preferred_model") or "")
    raw_ids = [str(value) for value in form.getlist("default_skill_ids")]

    model = (preferred_model or "").strip() or PUBLIC_DEFAULT_ID
    await get_models_list()
    try:
        resolve_upstream_model(model)
    except HTTPException as exc:
        return _settings_render(
            request,
            user,
            db,
            status_code=400,
            error=str(exc.detail),
        )

    _, err = set_default_skill_ids(db, user, raw_ids)
    if err:
        return _settings_render(
            request,
            user,
            db,
            status_code=400,
            error=err,
        )

    user.preferred_model = model
    db.commit()
    return _settings_render(request, user, db, saved=True)


@router.get("/tokens", response_class=HTMLResponse)
def tokens_page(
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/settings", status_code=303)


def _tokens_view_data(db: Session, user: User) -> tuple[list[ApiToken], dict[int, int]]:
    tokens = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    ).all()
    usage: dict[int, int] = {}
    if tokens:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = (
            db.execute(
                select(ApiTokenUsage).where(
                    ApiTokenUsage.token_id.in_([t.id for t in tokens]),
                    ApiTokenUsage.day == today,
                )
            )
            .scalars()
            .all()
        )
        usage = {r.token_id: r.count for r in rows}
    return tokens, usage


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

    active_count = db.scalar(
        select(func.count(ApiToken.id)).where(
            ApiToken.user_id == user.id,
            ApiToken.revoked_at.is_(None),
        )
    )
    if active_count >= settings.max_tokens_per_user:
        return _settings_render(
            request,
            user,
            db,
            token_error=(
                f"Достигнут лимит: не более {settings.max_tokens_per_user} "
                "токенов на пользователя. Отзовите один из активных токенов."
            ),
        )

    raw, prefix = create_api_token_raw()
    token = ApiToken(
        user_id=user.id,
        name=(name or "default").strip()[:100],
        token_hash=hash_token(raw),
        prefix=prefix,
    )
    db.add(token)
    db.commit()

    return _settings_render(request, user, db, new_token=raw)


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
    return RedirectResponse("/settings", status_code=303)
