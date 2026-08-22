from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import hash_token
from app.db import Conversation, ConversationSkill, Skill, SkillShare, User, UserSkillDefault
from app.notes import owned_conversation, parse_conversation_id

MAX_SKILL_BODY = 512 * 1024
MAX_SKILLS_ATTACHED = 10
DEFAULT_SKILL_TITLE = "Навык"
SKILL_TOO_LARGE = "Навык слишком большой"
TOO_MANY_SKILLS = "Слишком много навыков"
UNKNOWN_SKILL = "Неизвестный навык"


def parse_skill_id(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_title(title: str | None, fallback: str = DEFAULT_SKILL_TITLE) -> str:
    value = (title or "").strip()[:200]
    return value or fallback


def normalize_description(description: str | None) -> str:
    return (description or "").strip()[:500]


def _validate_body_len(body: str) -> str | None:
    if len(body.encode("utf-8")) > MAX_SKILL_BODY:
        return SKILL_TOO_LARGE
    return None


def _share_is_active(share: SkillShare, now: datetime | None = None) -> bool:
    if share.revoked_at is not None:
        return False
    if share.expires_at is None:
        return True
    now = now or _now()
    expires = share.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires >= now


def skill_is_public(skill: Skill, now: datetime | None = None) -> bool:
    now = now or _now()
    return any(_share_is_active(share, now) for share in skill.shares)


def catalog_url(skill_id: int) -> str:
    return f"/api/catalog/skills/{skill_id}"


def skill_payload(skill: Skill, *, public: bool | None = None) -> dict[str, Any]:
    is_public = skill_is_public(skill) if public is None else public
    return {
        "id": str(skill.id),
        "title": skill.title,
        "description": skill.description,
        "body": skill.body,
        "parent_id": str(skill.parent_id) if skill.parent_id is not None else None,
        "public": is_public,
        "created_at": _iso(skill.created_at),
        "updated_at": _iso(skill.updated_at),
    }


def catalog_summary(skill: Skill) -> dict[str, Any]:
    return {
        "id": str(skill.id),
        "title": skill.title,
        "description": skill.description,
        "updated_at": _iso(skill.updated_at),
    }


def catalog_detail(skill: Skill) -> dict[str, Any]:
    return {
        **catalog_summary(skill),
        "body": skill.body,
    }


def share_payload(skill: Skill, *, public: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {"public": public}
    if public:
        payload["url"] = catalog_url(skill.id)
    return payload


def owned_skill(db: Session, user: User, skill_id: int) -> Skill | None:
    return db.scalar(
        select(Skill)
        .where(Skill.id == skill_id, Skill.user_id == user.id)
        .options(selectinload(Skill.shares))
    )


def list_owned_skills(db: Session, user: User, q: str | None = None) -> list[Skill]:
    stmt = (
        select(Skill)
        .where(Skill.user_id == user.id)
        .options(selectinload(Skill.shares))
        .order_by(Skill.updated_at.desc(), Skill.id.desc())
    )
    term = (q or "").strip()[:100]
    if term:
        pattern = f"%{term}%"
        stmt = stmt.where(or_(Skill.title.like(pattern), Skill.description.like(pattern)))
    return list(db.scalars(stmt).all())


def create_skill(
    db: Session,
    user: User,
    *,
    title: str | None = None,
    description: str | None = None,
    body: str | None = None,
) -> tuple[Skill | None, str | None]:
    new_body = body if body is not None else ""
    err = _validate_body_len(new_body)
    if err:
        return None, err
    now = _now()
    skill = Skill(
        user_id=user.id,
        title=normalize_title(title),
        description=normalize_description(description),
        body=new_body,
        created_at=now,
        updated_at=now,
    )
    db.add(skill)
    db.flush()
    return skill, None


def update_skill(
    db: Session,
    skill: Skill,
    *,
    title: str | None = None,
    description: str | None = None,
    body: str | None = None,
) -> str | None:
    if title is not None:
        skill.title = normalize_title(title, fallback=skill.title)
    if description is not None:
        skill.description = normalize_description(description)
    if body is not None:
        err = _validate_body_len(body)
        if err:
            return err
        skill.body = body
    skill.updated_at = _now()
    return None


def active_share(db: Session, skill_id: int) -> SkillShare | None:
    shares = db.scalars(
        select(SkillShare)
        .where(SkillShare.skill_id == skill_id, SkillShare.revoked_at.is_(None))
        .order_by(SkillShare.created_at.desc())
    ).all()
    now = _now()
    for share in shares:
        if _share_is_active(share, now):
            return share
    return None


def publish_skill(db: Session, skill: Skill) -> SkillShare:
    existing = active_share(db, skill.id)
    if existing:
        return existing
    raw = "k_" + secrets.token_urlsafe(32)
    share = SkillShare(
        skill_id=skill.id,
        token_hash=hash_token(raw),
        prefix=raw[:10],
        created_at=_now(),
        expires_at=None,
    )
    db.add(share)
    db.flush()
    skill.shares.append(share)
    return share


def revoke_skill_share(db: Session, skill: Skill) -> None:
    now = _now()
    shares = db.scalars(
        select(SkillShare).where(
            SkillShare.skill_id == skill.id,
            SkillShare.revoked_at.is_(None),
        )
    ).all()
    for share in shares:
        share.revoked_at = now


def _public_skill_ids_subquery():
    now = _now()
    return (
        select(SkillShare.skill_id)
        .where(SkillShare.revoked_at.is_(None))
        .where(or_(SkillShare.expires_at.is_(None), SkillShare.expires_at >= now))
        .distinct()
    )


def list_catalog_skills(db: Session) -> list[Skill]:
    return list(
        db.scalars(
            select(Skill)
            .where(Skill.id.in_(_public_skill_ids_subquery()))
            .order_by(Skill.updated_at.desc(), Skill.id.desc())
        ).all()
    )


def catalog_skill(db: Session, skill_id: int) -> Skill | None:
    if active_share(db, skill_id) is None:
        return None
    return db.get(Skill, skill_id)


def copy_catalog_skill(db: Session, user: User, source: Skill) -> tuple[Skill | None, str | None]:
    copy, err = create_skill(
        db,
        user,
        title=source.title,
        description=source.description,
        body=source.body,
    )
    if err or copy is None:
        return copy, err
    copy.parent_id = source.id
    return copy, None


def _parse_owned_skill_ids(
    db: Session, user: User, raw_ids: list[str]
) -> tuple[list[int], str | None]:
    seen: set[int] = set()
    ordered: list[int] = []
    for raw in raw_ids:
        skill_id = parse_skill_id(raw)
        if skill_id is None or skill_id in seen:
            if skill_id is None:
                return [], UNKNOWN_SKILL
            continue
        seen.add(skill_id)
        ordered.append(skill_id)
    if len(ordered) > MAX_SKILLS_ATTACHED:
        return [], TOO_MANY_SKILLS
    if not ordered:
        return [], None
    found = {
        skill.id
        for skill in db.scalars(
            select(Skill).where(Skill.user_id == user.id, Skill.id.in_(ordered))
        ).all()
    }
    if found != set(ordered):
        return [], UNKNOWN_SKILL
    return ordered, None


def default_skill_ids(db: Session, user: User) -> list[str]:
    rows = db.scalars(
        select(UserSkillDefault)
        .where(UserSkillDefault.user_id == user.id)
        .order_by(UserSkillDefault.created_at, UserSkillDefault.skill_id)
    ).all()
    return [str(row.skill_id) for row in rows]


def set_default_skill_ids(
    db: Session, user: User, raw_ids: list[str]
) -> tuple[list[str], str | None]:
    ordered, err = _parse_owned_skill_ids(db, user, raw_ids)
    if err:
        return [], err
    db.execute(delete(UserSkillDefault).where(UserSkillDefault.user_id == user.id))
    db.flush()
    base = _now()
    for index, skill_id in enumerate(ordered):
        db.add(
            UserSkillDefault(
                user_id=user.id,
                skill_id=skill_id,
                created_at=base + timedelta(microseconds=index),
            )
        )
    db.flush()
    return [str(skill_id) for skill_id in ordered], None


def conversation_skill_ids(conversation: Conversation) -> list[str]:
    links = sorted(
        conversation.skill_links,
        key=lambda link: (link.created_at, link.skill_id),
    )
    return [str(link.skill_id) for link in links]


def copy_defaults_to_conversation(db: Session, user: User, conversation: Conversation) -> None:
    base = _now()
    for index, skill_id in enumerate(default_skill_ids(db, user)):
        db.add(
            ConversationSkill(
                conversation_id=conversation.id,
                skill_id=int(skill_id),
                created_at=base + timedelta(microseconds=index),
            )
        )


def set_conversation_skills(
    db: Session,
    user: User,
    conversation: Conversation,
    raw_ids: list[str],
) -> tuple[list[str], str | None]:
    ordered, err = _parse_owned_skill_ids(db, user, raw_ids)
    if err:
        return [], err
    conversation.skill_links.clear()
    db.flush()
    base = _now()
    for index, skill_id in enumerate(ordered):
        conversation.skill_links.append(
            ConversationSkill(
                conversation_id=conversation.id,
                skill_id=skill_id,
                created_at=base + timedelta(microseconds=index),
            )
        )
    return [str(skill_id) for skill_id in ordered], None


def skills_for_conversation(db: Session, conversation: Conversation) -> list[Skill]:
    ids = [int(skill_id) for skill_id in conversation_skill_ids(conversation)]
    if not ids:
        return []
    rows = db.scalars(select(Skill).where(Skill.id.in_(ids))).all()
    by_id = {row.id: row for row in rows}
    return [by_id[skill_id] for skill_id in ids if skill_id in by_id]


def skills_system_message(skills: list[Skill]) -> dict[str, str]:
    blocks: list[str] = []
    for skill in skills:
        parts = [f"## {skill.title}".strip()]
        if skill.description.strip():
            parts.append(skill.description.strip())
        if skill.body.strip():
            parts.append(skill.body.strip())
        blocks.append("\n\n".join(parts))
    content = (
        "Подключённые навыки. Следуй инструкциям каждого навыка, когда они относятся к запросу.\n\n"
        + "\n\n".join(blocks)
    )
    return {"role": "system", "content": content}


def inject_conversation_skills(
    body: dict[str, Any],
    db: Session,
    user: User,
) -> dict[str, Any]:
    conv_id = parse_conversation_id(body.get("conversation_id"))
    if conv_id is None:
        return body
    conversation = owned_conversation(db, user, conv_id)
    if conversation is None:
        return body
    db.refresh(conversation, attribute_names=["skill_links"])
    skills = skills_for_conversation(db, conversation)
    if not skills:
        return body
    messages = list(body.get("messages") or [])
    return {**body, "messages": [skills_system_message(skills), *messages]}
