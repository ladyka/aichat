"""Админка: оболочка на /admin и JSON API настроек моделей.

Страницу открывает только администратор (cookie-сессия, ``ADMIN_EMAILS``).
Само приложение — сборка ``admin/dist``, отдаётся как ``/admin-ui/``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
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
from app.routes.pages import render
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


class AdminModelsBody(BaseModel):
    use_env_default: bool = False
    use_env_allowed: bool = False
    default_model: str = ""
    unrestricted: bool = False
    allowed_models: list[str] = Field(default_factory=list)


def _session_user(request: Request, db: Session) -> User | None:
    settings = get_settings()
    return get_user_from_session(db, request.cookies.get(settings.session_cookie))


def _require_admin_page(request: Request, db: Session) -> User | RedirectResponse:
    user = _session_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/admin", status_code=303)
    if not is_admin(user):
        return RedirectResponse("/chat", status_code=303)
    return user


def _require_admin_api(request: Request, db: Session) -> User:
    user = _session_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Нет доступа к админке")
    return user


def _catalog_ids(payload: dict) -> list[str]:
    return [
        str(item["id"])
        for item in payload.get("data") or []
        if item.get("id") and item["id"] != PUBLIC_DEFAULT_ID
    ]


def _snapshot(catalog_ids: list[str]) -> dict:
    settings = get_settings()
    db_default, db_allowed = get_db_overrides()
    effective_allowed = get_effective_allowed_models()
    env_allowed = settings.allowed_models
    if effective_allowed is None:
        selected = list(catalog_ids)
    else:
        selected = sorted(effective_allowed)
    return {
        "env_default_model": settings.default_model or PUBLIC_DEFAULT_ID,
        "env_allowed_models": sorted(env_allowed) if env_allowed is not None else None,
        "db_default_model": db_default,
        "db_allowed_models": db_allowed,
        "use_env_default": db_default is None,
        "use_env_allowed": db_allowed is None,
        "effective_default_model": get_effective_default_model(),
        "unrestricted": effective_allowed is None,
        "selected_model_ids": selected,
        "catalog_ids": catalog_ids,
    }


async def _load_catalog_ids() -> list[str]:
    payload = await get_models_list_unfiltered()
    return _catalog_ids(payload)


def _save(db: Session, body: AdminModelsBody, catalog_ids: list[str]) -> None:
    catalog_set = set(catalog_ids)
    default_model = body.default_model.strip()

    if not body.use_env_default:
        if not default_model:
            raise HTTPException(
                status_code=400,
                detail="Укажите дефолтную модель или включите значение из окружения.",
            )
        try:
            resolve_upstream_model(default_model, enforce_allowlist=False)
        except HTTPException as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Дефолтная модель недоступна: {exc.detail}",
            ) from exc

    allowed_models: list[str] | None
    if body.use_env_allowed:
        allowed_models = None
    elif body.unrestricted:
        allowed_models = [ALLOWED_ALL_SENTINEL]
    else:
        raw_allowed = [item.strip() for item in body.allowed_models if item.strip()]
        unknown = [mid for mid in raw_allowed if mid not in catalog_set]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Неизвестные модели: {', '.join(unknown)}",
            )
        allowed_models = raw_allowed

    if (
        not body.use_env_default
        and not body.use_env_allowed
        and not body.unrestricted
        and default_model
        and default_model != PUBLIC_DEFAULT_ID
        and default_model not in (allowed_models or [])
    ):
        raise HTTPException(
            status_code=400,
            detail="Дефолтная модель должна входить в список разрешённых.",
        )

    save_model_settings(
        db,
        default_model=None if body.use_env_default else default_model,
        allowed_models=allowed_models,
        use_env_default=body.use_env_default,
        use_env_allowed=body.use_env_allowed,
    )
    invalidate_models_cache()


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, db: Session = Depends(get_db)):
    user_or_redirect = _require_admin_page(request, db)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    return render(request, "admin.html", user_or_redirect)


@router.get("/api/admin/models")
async def admin_models(request: Request, db: Session = Depends(get_db)):
    _require_admin_api(request, db)
    return _snapshot(await _load_catalog_ids())


@router.put("/api/admin/models")
async def admin_models_save(
    body: AdminModelsBody,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin_api(request, db)
    catalog_ids = await _load_catalog_ids()
    _save(db, body, catalog_ids)
    return _snapshot(catalog_ids)


@router.post("/api/admin/models/reset")
async def admin_models_reset(request: Request, db: Session = Depends(get_db)):
    _require_admin_api(request, db)
    reset_model_settings_to_env(db)
    invalidate_models_cache()
    return _snapshot(await _load_catalog_ids())
