"""Skills schema and HTTP API."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select, text

from app.auth import hash_token
from app.db import Conversation, ConversationSkill, Skill, SkillShare, User, UserSkillDefault
from app.skills import MAX_SKILL_BODY, MAX_SKILLS_ATTACHED
from tests.conftest import login, register


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
    db.commit()
    db.refresh(copy)
    db.add(UserSkillDefault(user_id=owner.id, skill_id=copy.id))
    conv = Conversation(user_id=owner.id, title="Новый чат")
    db.add(conv)
    db.commit()
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


def test_new_conversation_without_defaults_has_no_skills(client, db):
    """New chat copies user defaults; with none, the set is empty."""
    register(client, _email())
    created = client.post("/api/conversations", json={"title": "Без skills"}).json()
    assert created["skill_ids"] == []
    links = db.scalars(
        select(ConversationSkill).where(ConversationSkill.conversation_id == int(created["id"]))
    ).all()
    assert links == []


def _auth(client, addr=None):
    addr = addr or _email()
    response = register(client, addr)
    if response.status_code != 303:
        login(client, addr)
    return addr


def _create_skill(client, **fields):
    payload = {"title": "Пицца", "description": "pzz", "body": "Закажи пиццу."}
    payload.update(fields)
    return client.post("/api/skills", json=payload)


def test_skills_require_auth(client):
    assert client.get("/api/skills").status_code == 401
    assert client.post("/api/skills", json={"title": "x"}).status_code == 401
    assert client.get("/api/skills/1").status_code == 401
    assert client.patch("/api/skills/1", json={"body": "x"}).status_code == 401
    assert client.delete("/api/skills/1").status_code == 401
    assert client.get("/api/skills/1/share").status_code == 401
    assert client.post("/api/skills/1/share").status_code == 401
    assert client.post("/api/skills/1/share/revoke").status_code == 401
    assert client.post("/api/catalog/skills/1/copy").status_code == 401
    assert client.put("/api/conversations/1/skills", json={"skill_ids": []}).status_code == 401


def test_skill_crud_and_search(client):
    _auth(client)
    created = _create_skill(client)
    assert created.status_code == 200
    skill = created.json()
    assert skill["title"] == "Пицца"
    assert skill["description"] == "pzz"
    assert skill["body"] == "Закажи пиццу."
    assert skill["public"] is False
    assert skill["parent_id"] is None

    listed = client.get("/api/skills")
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1

    fetched = client.get(f"/api/skills/{skill['id']}")
    assert fetched.json()["body"] == skill["body"]

    patched = client.patch(
        f"/api/skills/{skill['id']}",
        json={"title": "Налоги", "description": "РБ", "body": "Поясни налог."},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Налоги"
    assert patched.json()["public"] is False

    found = client.get("/api/skills", params={"q": "Налог"})
    assert [item["id"] for item in found.json()["data"]] == [skill["id"]]
    assert client.get("/api/skills", params={"q": "пицца"}).json()["data"] == []

    deleted = client.delete(f"/api/skills/{skill['id']}")
    assert deleted.json() == {"ok": True}
    assert client.get(f"/api/skills/{skill['id']}").status_code == 404


def test_skill_not_visible_to_other_user(client):
    _auth(client)
    skill = _create_skill(client, body="secret").json()
    register(client, _email())
    assert client.get(f"/api/skills/{skill['id']}").status_code == 404
    assert client.patch(f"/api/skills/{skill['id']}", json={"body": "hack"}).status_code == 404
    assert client.delete(f"/api/skills/{skill['id']}").status_code == 404
    assert client.post(f"/api/skills/{skill['id']}/share").status_code == 404


def test_skill_too_large(client):
    _auth(client)
    body = "x" * (MAX_SKILL_BODY + 1)
    created = client.post("/api/skills", json={"body": body})
    assert created.status_code == 413
    skill = _create_skill(client).json()
    patched = client.patch(f"/api/skills/{skill['id']}", json={"body": body})
    assert patched.status_code == 413


def test_catalog_only_public_and_copy_is_private(client):
    _auth(client)
    private = _create_skill(client, title="Черновик", body="скрыто").json()
    public = _create_skill(client, title="Публичный", description="виден", body="текст").json()
    share = client.post(f"/api/skills/{public['id']}/share")
    assert share.status_code == 200
    assert share.json()["public"] is True
    assert share.json()["url"] == f"/api/catalog/skills/{public['id']}"
    assert client.get(f"/api/skills/{public['id']}/share").json()["public"] is True
    assert client.get(f"/api/skills/{public['id']}").json()["public"] is True

    anon_list = client.get("/api/catalog/skills")
    assert anon_list.status_code == 200
    ids = [item["id"] for item in anon_list.json()["data"]]
    assert public["id"] in ids
    assert private["id"] not in ids
    assert "body" not in anon_list.json()["data"][0]

    card = client.get(f"/api/catalog/skills/{public['id']}")
    assert card.status_code == 200
    assert card.json()["body"] == "текст"
    assert client.get(f"/api/catalog/skills/{private['id']}").status_code == 404

    client.patch(f"/api/skills/{public['id']}", json={"body": "обновлено"})
    assert client.get(f"/api/catalog/skills/{public['id']}").json()["body"] == "обновлено"

    register(client, _email())
    copy = client.post(f"/api/catalog/skills/{public['id']}/copy")
    assert copy.status_code == 200
    payload = copy.json()
    assert payload["parent_id"] == public["id"]
    assert payload["public"] is False
    assert payload["body"] == "обновлено"
    assert payload["id"] != public["id"]
    assert client.get(f"/api/catalog/skills/{payload['id']}").status_code == 404

    assert client.post(f"/api/catalog/skills/{private['id']}/copy").status_code == 404


def test_revoke_catalog_and_copy_survives(client):
    owner = _email()
    copier = _email()
    _auth(client, owner)
    skill = _create_skill(client, title="Open", body="live").json()
    client.post(f"/api/skills/{skill['id']}/share")
    _auth(client, copier)
    copy = client.post(f"/api/catalog/skills/{skill['id']}/copy").json()

    _auth(client, owner)
    revoked = client.post(f"/api/skills/{skill['id']}/share/revoke")
    assert revoked.json() == {"public": False}
    assert client.get(f"/api/catalog/skills/{skill['id']}").status_code == 404

    _auth(client, copier)
    kept = client.get(f"/api/skills/{copy['id']}")
    assert kept.status_code == 200
    assert kept.json()["parent_id"] == skill["id"]
    assert kept.json()["body"] == "live"


def test_defaults_copied_to_new_chat_and_can_change(client, mock_models):
    _auth(client)
    first = _create_skill(client, title="A", body="a").json()
    second = _create_skill(client, title="B", body="b").json()
    settings = client.put(
        "/api/settings",
        json={"default_skill_ids": [second["id"], first["id"]]},
    )
    assert settings.status_code == 200
    assert settings.json()["default_skill_ids"] == [second["id"], first["id"]]

    conv = client.post("/api/conversations", json={"title": "С дефолтами"}).json()
    assert conv["skill_ids"] == [second["id"], first["id"]]

    replaced = client.put(
        f"/api/conversations/{conv['id']}/skills",
        json={"skill_ids": [first["id"]]},
    )
    assert replaced.json()["skill_ids"] == [first["id"]]
    fetched = client.get(f"/api/conversations/{conv['id']}")
    assert fetched.json()["skill_ids"] == [first["id"]]

    emptied = client.put(f"/api/conversations/{conv['id']}/skills", json={"skill_ids": []})
    assert emptied.json()["skill_ids"] == []


def test_cannot_attach_foreign_or_too_many_skills(client, mock_models):
    _auth(client)
    own = _create_skill(client).json()
    register(client, _email())
    conv = client.post("/api/conversations", json={"title": "Чат"}).json()
    foreign = client.put(
        f"/api/conversations/{conv['id']}/skills",
        json={"skill_ids": [own["id"]]},
    )
    assert foreign.status_code == 400

    mine = [
        _create_skill(client, title=f"S{i}").json()["id"] for i in range(MAX_SKILLS_ATTACHED + 1)
    ]
    too_many = client.put(
        f"/api/conversations/{conv['id']}/skills",
        json={"skill_ids": mine},
    )
    assert too_many.status_code == 400
    settings = client.put("/api/settings", json={"default_skill_ids": mine})
    assert settings.status_code == 400


def test_chat_injects_attached_skills(client, mock_models, monkeypatch):
    _auth(client)
    skill = _create_skill(client, title="Налог", description="РБ", body="Ссылайся на НК РБ.").json()
    conv = client.post("/api/conversations", json={"title": "Чат"}).json()
    client.put(f"/api/conversations/{conv['id']}/skills", json={"skill_ids": [skill["id"]]})

    captured: dict = {}

    async def fake_stream(payload):
        captured["messages"] = payload["messages"]
        chunk = (
            'data: {"choices":[{"delta":{"content":"ok"}}],"model":"openrouter/free"}\n\n'
        ).encode("utf-8")
        yield chunk
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr("app.routes.api.stream_chat_completions", fake_stream)
    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "conversation_id": conv["id"],
            "messages": [{"role": "user", "content": "вопрос"}],
        },
    )
    assert response.status_code == 200
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert "Налог" in messages[0]["content"]
    assert "НК РБ" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "вопрос"}

    captured.clear()
    client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "без чата"}],
        },
    )
    assert captured["messages"][0]["role"] == "user"


def test_v1_does_not_inject_skills(client, mock_models, monkeypatch):
    from tests.conftest import create_token

    _auth(client)
    skill = _create_skill(client, body="не должно уйти в v1").json()
    conv = client.post("/api/conversations", json={"title": "Чат"}).json()
    client.put(f"/api/conversations/{conv['id']}/skills", json={"skill_ids": [skill["id"]]})
    token, _ = create_token(client)
    captured: dict = {}

    async def fake_chat(payload):
        captured["messages"] = payload["messages"]

        class Resp:
            status_code = 200

            def json(self):
                return {
                    "id": "cmpl-test",
                    "object": "chat.completion",
                    "model": "openrouter/free",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }

        return Resp()

    monkeypatch.setattr("app.routes.api.chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "default",
            "conversation_id": conv["id"],
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert captured["messages"][0]["role"] == "user"
    assert "не должно уйти в v1" not in json.dumps(captured["messages"], ensure_ascii=False)
