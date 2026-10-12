"""Глобальные настройки витрины: дефолтная модель и белый список.

Значения по умолчанию берутся из переменных окружения (`DEFAULT_MODEL`,
`ALLOWED_MODELS`). Админка может записать переопределения в таблицу
`app_settings` (одна строка с id=1). Колонка ``None`` снова отдаёт управление
окружению.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import AppSettings, SessionLocal, User

APP_SETTINGS_ID = 1
# В JSON белого списка: единственный элемент «*» значит «все модели»
# (переопределение поверх непустого ALLOWED_MODELS из окружения).
ALLOWED_ALL_SENTINEL = "*"

# Кеш переопределений из БД. ``_UNSET`` — ещё не читали; ``None`` в поле —
# «в БД пусто, брать env».
_UNSET = object()
_override_default: Any = _UNSET
_override_allowed: Any = _UNSET


def is_admin(user: User | None) -> bool:
    if user is None:
        return False
    emails = get_settings().admin_emails
    if not emails:
        return False
    return (user.email or "").strip().lower() in emails


def invalidate_overrides_cache() -> None:
    global _override_default, _override_allowed
    _override_default = _UNSET
    _override_allowed = _UNSET


def _load_row(db: Session) -> AppSettings | None:
    return db.get(AppSettings, APP_SETTINGS_ID)


def refresh_overrides(db: Session | None = None) -> None:
    """Прочитать строку app_settings в кеш процесса."""
    global _override_default, _override_allowed
    owns = db is None
    session = db if db is not None else SessionLocal()
    try:
        row = _load_row(session)
        if row is None:
            _override_default = None
            _override_allowed = None
            return
        raw_default = (row.default_model or "").strip() or None
        raw_allowed = row.allowed_models
        if raw_allowed is None:
            allowed: list[str] | None = None
        elif isinstance(raw_allowed, list):
            allowed = [str(item).strip() for item in raw_allowed if str(item).strip()]
        else:
            allowed = None
        _override_default = raw_default
        _override_allowed = allowed
    except Exception:  # noqa: BLE001 — нет таблицы / БД ещё не поднята → только env
        _override_default = None
        _override_allowed = None
    finally:
        if owns:
            session.close()


def _ensure_loaded() -> None:
    if _override_default is _UNSET or _override_allowed is _UNSET:
        refresh_overrides()


def get_db_overrides() -> tuple[str | None, list[str] | None]:
    """Переопределения из БД как есть: (default_model, allowed_models)."""
    _ensure_loaded()
    default = None if _override_default is _UNSET else _override_default
    allowed = None if _override_allowed is _UNSET else _override_allowed
    return default, allowed


def get_effective_default_model() -> str:
    """Цель публичного id ``default``: БД, иначе ``DEFAULT_MODEL``."""
    _ensure_loaded()
    if isinstance(_override_default, str) and _override_default.strip():
        return _override_default.strip()
    settings = get_settings()
    return (settings.default_model or "").strip() or "default"


def get_effective_allowed_models() -> frozenset[str] | None:
    """Белый список публичных id. ``None`` — без ограничения."""
    _ensure_loaded()
    if _override_allowed is not _UNSET and _override_allowed is not None:
        cleaned = [str(item).strip() for item in _override_allowed if str(item).strip()]
        if ALLOWED_ALL_SENTINEL in cleaned:
            return None
        return frozenset(cleaned)
    return get_settings().allowed_models


def model_is_allowed(public_id: str) -> bool:
    """Публичный id ``default`` всегда разрешён, если маршрут существует."""
    model = (public_id or "").strip()
    if not model or model == "default":
        return True
    allowed = get_effective_allowed_models()
    if allowed is None:
        return True
    return model in allowed


def save_model_settings(
    db: Session,
    *,
    default_model: str | None,
    allowed_models: list[str] | None,
    use_env_default: bool = False,
    use_env_allowed: bool = False,
) -> AppSettings:
    """Сохранить переопределения. ``use_env_*`` обнуляет колонку (снова env)."""
    row = _load_row(db)
    if row is None:
        row = AppSettings(id=APP_SETTINGS_ID)
        db.add(row)

    if use_env_default:
        row.default_model = None
    else:
        cleaned = (default_model or "").strip() or None
        row.default_model = cleaned

    if use_env_allowed:
        row.allowed_models = None
    else:
        if allowed_models is None:
            row.allowed_models = None
        else:
            row.allowed_models = [str(item).strip() for item in allowed_models if str(item).strip()]

    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    refresh_overrides(db)
    return row


def reset_model_settings_to_env(db: Session) -> None:
    """Удалить переопределения — снова только переменные окружения."""
    row = _load_row(db)
    if row is not None:
        db.delete(row)
        db.commit()
    invalidate_overrides_cache()
    refresh_overrides(db)
