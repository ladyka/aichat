"""ORM-level checks for skills schema (API comes later)."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text

from app.auth import hash_token
from app.db import Conversation, ConversationSkill, Skill, SkillShare, User, UserSkillDefault
from tests.conftest import register


def _email():
    return f"skill-{uuid.uuid4().hex[:8]}@example.com"


def test_skill_copy_defaults_chat_and_share(client, db):
    register(client, _email())
    owner = db.scalar(select(User).order_by(User.id.desc()))
    assert owner is not None

    original = Skill(
        user_id=owner.id,
        title="Пицца",
        description="Заказ с pzz.by",
        body="Помоги заказать пиццу.",
    )
    db.add(original)
    db.commit()
    db.refresh(original)

    copy = Skill(
        user_id=owner.id,
        parent_id=original.id,
        title="Пицца",
        description="Заказ с pzz.by",
        body="Помоги заказать пиццу.",
    )
    db.add(copy)
    db.add(UserSkillDefault(user_id=owner.id, skill_id=copy.id))
    conv = Conversation(user_id=owner.id, title="Новый чат")
    db.add(conv)
    db.commit()
    db.refresh(copy)
    db.refresh(conv)

    db.add(ConversationSkill(conversation_id=conv.id, skill_id=copy.id))
    db.add(
        SkillShare(
            skill_id=original.id,
            token_hash=hash_token("sk_test_catalog_key"),
            prefix="sk_test_ca",
        )
    )
    db.commit()

    db.refresh(copy)
    db.refresh(original)
    assert copy.parent_id == original.id
    assert copy.parent.id == original.id
    assert original.copies[0].id == copy.id
    assert db.scalar(
        select(UserSkillDefault).where(
            UserSkillDefault.user_id == owner.id,
            UserSkillDefault.skill_id == copy.id,
        )
    )
    assert db.scalar(
        select(ConversationSkill).where(
            ConversationSkill.conversation_id == conv.id,
            ConversationSkill.skill_id == copy.id,
        )
    )
    share = db.scalar(select(SkillShare).where(SkillShare.skill_id == original.id))
    assert share is not None
    assert share.revoked_at is None


def test_deleting_parent_skill_nulls_parent_id(client, db):
    register(client, _email())
    owner = db.scalar(select(User).order_by(User.id.desc()))
    parent = Skill(user_id=owner.id, title="A", body="a")
    db.add(parent)
    db.commit()
    db.refresh(parent)
    child = Skill(user_id=owner.id, parent_id=parent.id, title="A copy", body="a")
    db.add(child)
    db.commit()
    child_id = child.id

    db.execute(text("PRAGMA foreign_keys=ON"))
    db.delete(parent)
    db.commit()

    leftover = db.get(Skill, child_id)
    assert leftover is not None
    assert leftover.parent_id is None


def test_deleting_skill_drops_defaults_and_chat_links(client, db):
    register(client, _email())
    owner = db.scalar(select(User).order_by(User.id.desc()))
    skill = Skill(user_id=owner.id, title="B", body="b")
    db.add(skill)
    conv = Conversation(user_id=owner.id, title="Чат")
    db.add(conv)
    db.commit()
    db.refresh(skill)
    db.refresh(conv)
    db.add(UserSkillDefault(user_id=owner.id, skill_id=skill.id))
    db.add(ConversationSkill(conversation_id=conv.id, skill_id=skill.id))
    db.commit()
    skill_id = skill.id

    db.delete(skill)
    db.commit()

    assert db.scalar(select(Skill).where(Skill.id == skill_id)) is None
    assert db.scalar(select(UserSkillDefault).where(UserSkillDefault.skill_id == skill_id)) is None
    assert (
        db.scalar(select(ConversationSkill).where(ConversationSkill.skill_id == skill_id)) is None
    )
    assert db.get(Conversation, conv.id) is not None


def test_new_conversation_does_not_auto_attach_skills(client, db):
    """Variant 2: defaults are copied in the conversations API, not by the ORM."""
    register(client, _email())
    created = client.post("/api/conversations", json={"title": "Без skills"}).json()
    links = db.scalars(
        select(ConversationSkill).where(ConversationSkill.conversation_id == int(created["id"]))
    ).all()
    assert links == []
