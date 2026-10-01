"""Сайты пользователей на mzg.by: домен третьего уровня <domain>.mzg.by.

У пользователя не более одного сайта; сайт — один markdown-файл.
Всё, что связано с mzg.by, живёт здесь: нормализация и проверка домена,
создание, изменение домена, публикация и удаление (см. app/tools.py —
инструменты create_site / update_site / publish_site / delete_site).
После удаления домен освобождается: имя может занять любой пользователь.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import UserSite

logger = logging.getLogger("aichat.mzg")

MZG_SUFFIX = "mzg.by"

# Метка: строчные буквы/цифры и дефисы, без ведущего и замыкающего дефиса,
# 3–63 символа (правило одного имени DNS).
_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{3,63}(?<!-)$")

# Резервные имена, которые нельзя отдать пользователю.
_FORBIDDEN = frozenset(
    {"www", "mail", "ftp", "api", "admin", "ns1", "ns2", "web", "static", "aichat"}
)

MAX_SITE_BODY = 512 * 1024

DOMAIN_INVALID = "Недопустимый домен"
DOMAIN_TAKEN = "Такой домен уже занят"
SITE_NOT_FOUND = "Сайт не создан"
SITE_TOO_LARGE = "Сайт слишком большой"


def site_domain(raw: str) -> str:
    """Нормализовать имя в домен <name>.mzg.by: нижний регистр, без точки-суффикса."""
    value = (raw or "").strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0].strip(".")
    if value.endswith(f".{MZG_SUFFIX}"):
        value = value[: -len(f".{MZG_SUFFIX}")].strip(".")
    return value


def validate_domain(value: str) -> str | None:
    """Проверить нормализованное имя (суффикс уже срезан). None — домен годен."""
    if not value or len(value) < 3 or len(value) > 63:
        return DOMAIN_INVALID
    if not _DOMAIN_RE.match(value):
        return DOMAIN_INVALID
    if value in _FORBIDDEN:
        return DOMAIN_INVALID
    return None


def user_site(db: Session, user: Any) -> UserSite | None:
    return db.scalar(select(UserSite).where(UserSite.user_id == user.id))


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def get_site_by_domain(db: Session, domain: str) -> UserSite | None:
    return db.scalar(select(UserSite).where(UserSite.domain == domain))


def create_site(db: Session, user: Any, raw_domain: str) -> tuple[UserSite | None, str | None]:
    """Зарегистрировать сайт; у пользователя он единственный. Returns (site, error)."""
    domain = site_domain(raw_domain)
    err = validate_domain(domain)
    if err:
        return None, err
    existing = user_site(db, user)
    if existing is not None:
        return (
            None,
            f"У вас уже есть сайт {existing.domain}.{MZG_SUFFIX}. "
            "Один сайт на пользователя; удалите его (delete_site), чтобы занять новый.",
        )
    if get_site_by_domain(db, domain) is not None:
        return None, DOMAIN_TAKEN
    site = UserSite(
        user_id=user.id,
        domain=domain,
        created_at=datetime.now(timezone.utc),
    )
    db.add(site)
    try:
        db.flush()
    except Exception:
        db.rollback()
        # Параллельная регистрация того же домена.
        return None, DOMAIN_TAKEN
    logger.info("site created user_id=%s domain=%s", user.id, domain)
    return site, None


def update_site(db: Session, user: Any, raw_domain: str) -> tuple[UserSite | None, str | None]:
    """Сменить домен: алиас двух операций — удалить старый сайт и создать новый.

    Файл публикации не переносится: он стирается вместе со старым сайтом,
    новый сайт нужно опубликовать заново из заметки чата.
    """
    new_domain = site_domain(raw_domain)
    err = validate_domain(new_domain)
    if err:
        return None, err
    old = user_site(db, user)
    if old is None:
        return None, SITE_NOT_FOUND
    if new_domain != old.domain and get_site_by_domain(db, new_domain) is not None:
        return None, DOMAIN_TAKEN
    old_domain = old.domain
    deleted, err = delete_site(db, user)
    if err:
        return None, err
    site, err = create_site(db, user, new_domain)
    if err:
        # delete_site уже закоммичен; остаёмся в согласованном состоянии
        # (сайт не создан, домен старый свободен — создать заново можно всегда).
        return None, err
    db.commit()
    logger.info("site updated user_id=%s %s -> %s", user.id, old_domain, site.domain)
    return site, None


def delete_site(db: Session, user: Any) -> tuple[UserSite | None, str | None]:
    """Удалить сайт: строка в БД и опубликованный файл; домен освобождается."""
    site = user_site(db, user)
    if site is None:
        return None, SITE_NOT_FOUND
    path = _site_file(site.domain)
    if path is not None and path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("site delete file unlink failed domain=%s error=%s", site.domain, exc)
    db.delete(site)
    db.commit()
    logger.info("site deleted user_id=%s domain=%s", user.id, site.domain)
    return site, None


def _read_site_file(path: Path, site: UserSite) -> str:
    """Текст <domain>.md; ошибка чтения — пустая строка."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("site_status read failed domain=%s error=%s", site.domain, exc)
        return ""


def site_status(db: Session, user: Any) -> dict[str, Any]:
    """Состояние сайта пользователя для инструмента site_status (без изменений в БД).

    сайта нет → {"exists": False}; иначе домен, отметки времени, флаг публикации
    и сведения о файле <domain>.md на диске (file_exists/file_bytes).
    Содержимое (body) — файл публикации: то, что реально увидит посетитель.
    """
    site = user_site(db, user)
    if site is None:
        return {"exists": False}
    file_path = _site_file(site.domain)
    file_exists = bool(file_path is not None and file_path.is_file())
    payload: dict[str, Any] = {
        "exists": True,
        "domain": f"{site.domain}.{MZG_SUFFIX}",
        "created_at": _iso(site.created_at),
        "published": site.published_at is not None,
        "published_at": _iso(site.published_at),
    }
    payload["file_exists"] = file_exists
    payload["file_bytes"] = file_path.stat().st_size if file_exists else 0
    if not file_exists and site.published_at is not None:
        payload["note"] = "файл публикации отсутствует на диске; опубликуйте заново"
    if file_exists:
        payload["body"] = _read_site_file(file_path, site)
    return payload


def sites_folder() -> Path:
    folder = Path(get_settings().mzg_sites_folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _site_file(domain: str) -> Path | None:
    if not get_settings().mzg_sites_folder:
        return None
    return sites_folder() / f"{domain}.md"


def publish_site(db: Session, user: Any, body: str) -> tuple[UserSite | None, str | None]:
    """Записать markdown сайта в MZG_SITES_FOLDER/<domain>.md.

    Тело — заметка чата или явный body из tools.py. Обновление файла
    оверврайтит прежнюю публикацию.
    """
    site = user_site(db, user)
    if site is None:
        return None, SITE_NOT_FOUND
    if len((body or "").encode("utf-8")) > MAX_SITE_BODY:
        return None, SITE_TOO_LARGE
    try:
        path = sites_folder() / f"{site.domain}.md"
        path.write_text(body or "", encoding="utf-8")
    except OSError as exc:
        logger.warning("publish failed user_id=%s domain=%s error=%s", user.id, site.domain, exc)
        return None, "Не удалось записать файл сайта"
    site.published_at = datetime.now(timezone.utc)
    db.commit()
    logger.info(
        "site published user_id=%s domain=%s bytes=%s",
        user.id,
        site.domain,
        len((body or "").encode("utf-8")),
    )
    return site, None
