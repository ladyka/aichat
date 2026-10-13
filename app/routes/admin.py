"""Админка: дефолтная модель и белый список моделей."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_user_from_session
from app.config import get_settings
from app.db import User, get_db
from app.models_catalog import (
    PUBLIC_DEFAULT_ID,
    get_models_list_unfiltered,
    invalidate_models_cache,
    resolve_upstream_model,
)
from app.og import og_context
from app.site_settings import (
    ALLOWED_ALL_SENTINEL,
    get_db_overrides,
    get_effective_allowed_models,
    get_effective_default_model,
    is_admin,
    reset_model_settings_to_env,
    save_model_settings,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(get_settings().root / "templates"))


def _require_admin(request: Request, db: Session) -> User | RedirectResponse:
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if not user:
        return RedirectResponse("/login?next=/admin", status_code=303)
    if not is_admin(user):
        return RedirectResponse("/chat", status_code=303)
    return user


def _render(
    request: Request,
    user: User,
    *,
    error: str | None = None,
    saved: bool = False,
    status_code: int = 200,
    catalog_ids: list[str] | None = None,
):
    settings = get_settings()
    db_default, db_allowed = get_db_overrides()
    effective_default = get_effective_default_model()
    effective_allowed = get_effective_allowed_models()
    env_allowed = settings.allowed_models
    selected: list[str]
    if effective_allowed is None:
        selected = list(catalog_ids or [])
    else:
        selected = sorted(effective_allowed)
    ctx = {
        "request": request,
        "user": user,
        "app_name": "aichat",
        "error": error,
        "saved": saved,
        "env_default_model": settings.default_model or PUBLIC_DEFAULT_ID,
        "env_allowed_models": sorted(env_allowed) if env_allowed is not None else None,
        "db_default_model": db_default,
        "db_allowed_models": db_allowed,
        "effective_default_model": effective_default,
        "use_env_default": db_default is None,
        "use_env_allowed": db_allowed is None,
        "unrestricted": effective_allowed is None,
        "selected_model_ids": selected,
        "catalog_ids": catalog_ids or [],
        "default_model": effective_default,
        **og_context(request),
    }
    return templates.TemplateResponse(
        request,
        "admin.html",
        ctx,
        status_code=status_code,
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: Session = Depends(get_db)):
    user_or_redirect = _require_admin(request, db)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    payload = await get_models_list_unfiltered()
    catalog_ids = [
        str(item["id"])
        for item in payload.get("data") or []
        if item.get("id") and item["id"] != PUBLIC_DEFAULT_ID
    ]
    return _render(request, user_or_redirect, catalog_ids=catalog_ids)


@router.post("/admin", response_class=HTMLResponse)
async def admin_submit(request: Request, db: Session = Depends(get_db)):
    user_or_redirect = _require_admin(request, db)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect

    form = await request.form()
    action = str(form.get("action") or "save")

    payload = await get_models_list_unfiltered()
    catalog_ids = [
        str(item["id"])
        for item in payload.get("data") or []
        if item.get("id") and item["id"] != PUBLIC_DEFAULT_ID
    ]
    catalog_set = set(catalog_ids)

    if action == "reset":
        reset_model_settings_to_env(db)
        invalidate_models_cache()
        return _render(request, user, saved=True, catalog_ids=catalog_ids)

    use_env_default = str(form.get("use_env_default") or "") == "1"
    use_env_allowed = str(form.get("use_env_allowed") or "") == "1"
    default_model = str(form.get("default_model") or "").strip()
    raw_allowed = [
        str(value).strip() for value in form.getlist("allowed_models") if str(value).strip()
    ]
    unrestricted = str(form.get("unrestricted") or "") == "1"

    if not use_env_default:
        if not default_model:
            return _render(
                request,
                user,
                status_code=400,
                error="Укажите дефолтную модель или включите значение из окружения.",
                catalog_ids=catalog_ids,
            )
        try:
            resolve_upstream_model(default_model, enforce_allowlist=False)
        except HTTPException as exc:
            return _render(
                request,
                user,
                status_code=400,
                error=f"Дефолтная модель недоступна: {exc.detail}",
                catalog_ids=catalog_ids,
            )

    allowed_models: list[str] | None
    if use_env_allowed:
        allowed_models = None
    elif unrestricted:
        allowed_models = [ALLOWED_ALL_SENTINEL]
    else:
        unknown = [mid for mid in raw_allowed if mid not in catalog_set]
        if unknown:
            return _render(
                request,
                user,
                status_code=400,
                error=f"Неизвестные модели: {', '.join(unknown)}",
                catalog_ids=catalog_ids,
            )
        allowed_models = raw_allowed

    if (
        not use_env_default
        and not use_env_allowed
        and not unrestricted
        and default_model
        and default_model != PUBLIC_DEFAULT_ID
        and default_model not in (allowed_models or [])
    ):
        return _render(
            request,
            user,
            status_code=400,
            error="Дефолтная модель должна входить в список разрешённых.",
            catalog_ids=catalog_ids,
        )

    save_model_settings(
        db,
        default_model=None if use_env_default else default_model,
        allowed_models=allowed_models,
        use_env_default=use_env_default,
        use_env_allowed=use_env_allowed,
    )
    invalidate_models_cache()
    return _render(request, user, saved=True, catalog_ids=catalog_ids)
