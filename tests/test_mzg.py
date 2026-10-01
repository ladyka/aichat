"""Сайты на mzg.by: create/update/delete/publish (tools) и app/mzg.py."""

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from app.config import get_settings
from app.db import User, get_user_by_email
from app.mzg import (
    DOMAIN_INVALID,
    DOMAIN_TAKEN,
    MAX_SITE_BODY,
    MZG_SUFFIX,
    SITE_NOT_FOUND,
    SITE_TOO_LARGE,
    create_site,
    delete_site,
    publish_site,
    site_domain,
    sites_folder,
    update_site,
    user_site,
    validate_domain,
)
from app.tools import call_tool, enabled_tools
from tests.conftest import register
from tests.test_tools import StreamPlan, _patch, _sse_text, _sse_tool_call


def _email():
    return f"sites-{uuid.uuid4().hex[:8]}@example.com"


def _enable(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "mzg_sites_folder", str(tmp_path / "sites"))


def _user_ctx(client, db, email=None):
    email = email or _email()
    register(client, email)
    user = get_user_by_email(db, email)
    conv = client.post("/api/conversations", json={"title": "s"}).json()
    return user, int(conv["id"])


def _call(name, arguments="", user=None, db=None, conversation_id=None):
    return json.loads(
        asyncio.run(call_tool(name, arguments, user=user, db=db, conversation_id=conversation_id))
    )


# --- домены


@pytest.fixture(autouse=True)
def _isolate_sites_folder(tmp_path, monkeypatch):
    """Каждый тест — своя папка сайтов: файлы не падают в корень репозитория."""
    monkeypatch.setattr(get_settings(), "mzg_sites_folder", str(tmp_path / "sites"))
    yield


def test_site_domain_normalizes():
    assert site_domain("  My-Page ") == "my-page"
    assert site_domain("https://mysite.mzg.by/") == "mysite"
    assert site_domain("mysite.mzg.by") == "mysite"
    assert site_domain("") == ""


def test_validate_domain_rules():
    assert validate_domain("my-page") is None
    assert validate_domain("a1b2c") is None
    assert validate_domain("") == DOMAIN_INVALID
    assert validate_domain("-nope") == DOMAIN_INVALID
    assert validate_domain("nope-") == DOMAIN_INVALID
    assert validate_domain("ab") == DOMAIN_INVALID  # < 3
    assert validate_domain("a" * 64) == DOMAIN_INVALID  # > 63
    assert validate_domain("два слова") == DOMAIN_INVALID
    assert validate_domain("www") == DOMAIN_INVALID
    assert validate_domain("api") == DOMAIN_INVALID


# --- модуль


def test_create_site_and_unique_per_user_and_domain(client, db):
    user, _ = _user_ctx(client, db)
    site, err = create_site(db, user, "My-Site")
    assert err is None
    assert site.domain == "my-site"
    db.expire_all()
    again, err = create_site(db, user, "other")
    assert again is None
    assert "уже есть сайт my-site" in err

    clash, err = create_site(db, type("U", (), {"id": user.id + 1000})(), "my-site")
    assert clash is None
    assert err == DOMAIN_TAKEN


def test_update_site_is_delete_plus_create(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    create_site(db, user, "old-name")
    publish_site(db, user, "# v1")

    site, err = update_site(db, user, "new-name")
    assert err is None
    assert site.domain == "new-name"
    # Алиас delete + create: публикация не переносится, файл старого стёрт.
    assert site.published_at is None
    assert not (sites_folder() / "old-name.md").exists()
    assert not (sites_folder() / "new-name.md").exists()

    # Прежний домен освободился и может занять другой пользователь.
    stranger = type("U", (), {"id": user.id + 7000})()
    taken, err = create_site(db, stranger, "old-name")
    assert err is None
    assert taken.domain == "old-name"


def test_update_site_keeps_domain_when_same(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    create_site(db, user, "stable")
    db.expire_all()
    site, err = update_site(db, user, "stable.mzg.by")
    assert err is None
    assert site.domain == "stable"


def test_update_site_to_taken_or_invalid_fails(client, db, tmp_path):
    other_user = type("U", (), {"id": 4242})()
    create_site(db, other_user, "busy")
    db.commit()
    user, _ = _user_ctx(client, db)
    create_site(db, user, "mine")

    clash, err = update_site(db, user, "busy")
    assert clash is None
    assert err == DOMAIN_TAKEN
    bad, err = update_site(db, user, "нет")
    assert bad is None
    assert err == DOMAIN_INVALID


def test_update_site_without_site_fails(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    site, err = update_site(db, user, "ghost")
    assert site is None
    assert err == SITE_NOT_FOUND


def test_update_moves_unpublished_file_only_if_present(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    create_site(db, user, "fresh")
    site, err = update_site(db, user, "renamed")
    assert err is None
    assert site.published_at is None
    assert not (sites_folder() / "fresh.md").exists()


def test_update_failure_leaves_site_deleted_but_domain_free(client, db, tmp_path):
    """Провал create после delete: сайт удалён, пользователь может создать новый."""
    import unittest.mock as mock

    import app.mzg as mzg_mod

    user, _ = _user_ctx(client, db)
    create_site(db, user, "before")
    db.commit()

    with mock.patch.object(mzg_mod, "create_site", return_value=(None, DOMAIN_TAKEN)):
        site, err = update_site(db, user, "after")
    assert site is None
    assert err == DOMAIN_TAKEN
    db.expire_all()
    # Старый сайт удалён, его домен свободен; создать новый можно всегда.
    assert user_site(db, user) is None
    again, err = create_site(db, user, "recovered")
    assert err is None
    assert again.domain == "recovered"


def test_delete_site_frees_domain_and_file(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    create_site(db, user, "gone")
    publish_site(db, user, "# bye")
    assert (sites_folder() / "gone.md").exists()

    site, err = delete_site(db, user)
    assert err is None
    assert site.domain == "gone"
    assert user_site(db, user) is None
    assert not (sites_folder() / "gone.md").exists()

    # Освободившийся домен может занять другой пользователь.
    stranger = type("U", (), {"id": user.id + 5000})()
    taken, err = create_site(db, stranger, "gone")
    assert err is None
    assert taken.domain == "gone"

    # Прежний владелец может снова создать сайт под любым именем.
    again, err = create_site(db, user, "phoenix")
    assert err is None
    assert again.domain == "phoenix"


def test_delete_site_without_site_fails(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    site, err = delete_site(db, user)
    assert site is None
    assert err == SITE_NOT_FOUND


def test_publish_writes_domain_md(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    create_site(db, user, "cookbook")
    site, err = publish_site(db, user, "# Recipes\n\n- борщ")
    assert err is None
    assert site.published_at is not None
    path = sites_folder() / "cookbook.md"
    assert path.read_text(encoding="utf-8") == "# Recipes\n\n- борщ"


def test_publish_without_site_fails(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    site, err = publish_site(db, user, "# x")
    assert site is None
    assert err == SITE_NOT_FOUND
    assert list(Path(tmp_path / "sites").glob("*.md")) == []


def test_publish_too_large(client, db, tmp_path):
    user, _ = _user_ctx(client, db)
    create_site(db, user, "big")
    site, err = publish_site(db, user, "x" * (MAX_SITE_BODY + 1))
    assert site is None
    assert err == SITE_TOO_LARGE


def test_user_site_relationship_cascade(client, db):
    from app.db import UserSite as Site_

    user, _ = _user_ctx(client, db)
    create_site(db, user, "cascade")
    db.commit()
    db.expire_all()
    row = db.get(User, user.id)
    assert isinstance(row.site, Site_)
    assert row.site.domain == "cascade"


# --- tools


def test_sites_tools_off_without_folder(monkeypatch):
    monkeypatch.setattr(get_settings(), "mzg_sites_folder", "")
    names = [t["function"]["name"] for t in enabled_tools()]
    assert "create_site" not in names
    assert "update_site" not in names
    assert "delete_site" not in names
    assert "publish_site" not in names
    assert "site_status" not in names


def test_sites_tools_advertised_with_folder(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    names = [t["function"]["name"] for t in enabled_tools()]
    assert "create_site" in names
    assert "update_site" in names
    assert "delete_site" in names
    assert "publish_site" in names
    assert "site_status" in names


def test_sites_tools_disabled_error(monkeypatch):
    monkeypatch.setattr(get_settings(), "mzg_sites_folder", "")
    result = _call("create_site", '{"domain": "x1"}', user=object(), db=object())
    assert "не настроена" in result["error"]


def test_create_site_tool_flow(monkeypatch, client, db, tmp_path):
    _enable(monkeypatch, tmp_path)
    user, _ = _user_ctx(client, db)

    bad = _call("create_site", '{"domain": "a"}', user=user, db=db)
    assert bad["error"] == DOMAIN_INVALID

    ok = _call("create_site", '{"domain": "myblog"}', user=user, db=db)
    assert ok == {"ok": True, "domain": "myblog.mzg.by", "published": False}
    db.expire_all()
    assert user_site(db, user).domain == "myblog"

    second = _call("create_site", '{"domain": "other9"}', user=user, db=db)
    assert "уже есть сайт" in second["error"]


def test_update_site_tool_flow(monkeypatch, client, db, tmp_path):
    _enable(monkeypatch, tmp_path)
    user, _ = _user_ctx(client, db)
    create_site(db, user, "first")
    changed = _call("update_site", '{"domain": "second"}', user=user, db=db)
    assert changed == {
        "ok": True,
        "domain": "second.mzg.by",
        "published": False,
    }

    missing = _call("update_site", '{"domain": "x1y2"}', user=type("U", (), {"id": -1})(), db=db)
    assert missing["error"] == SITE_NOT_FOUND


def test_delete_site_tool_needs_confirm(monkeypatch, client, db, tmp_path):
    _enable(monkeypatch, tmp_path)
    user, _ = _user_ctx(client, db)
    create_site(db, user, "victim")
    preview = _call("delete_site", "{}", user=user, db=db)
    assert preview["confirm"] is False
    assert preview["domain"] == "victim.mzg.by"
    assert user_site(db, user).domain == "victim"
    deleted = _call("delete_site", '{"confirm": true}', user=user, db=db)
    assert deleted == {"ok": True, "deleted": "victim.mzg.by", "domain": None}
    assert user_site(db, user) is None
    none = _call("delete_site", "{}", user=user, db=db)
    assert none["error"] == SITE_NOT_FOUND


def test_site_status_lifecycle(monkeypatch, client, db, tmp_path):
    _enable(monkeypatch, tmp_path)
    user, _ = _user_ctx(client, db)

    # Сайта нет.
    empty = _call("site_status", "{}", user=user, db=db)
    assert empty == {"exists": False}

    # Создан, но не публиковался.
    _call("create_site", '{"domain": "statusy"}', user=user, db=db)
    created = _call("site_status", "{}", user=user, db=db)
    assert created["exists"] is True
    assert created["domain"] == "statusy.mzg.by"
    assert created["published"] is False
    assert created["file_exists"] is False
    assert created["published_at"] is None
    assert created["created_at"]

    # После публикации: флаг, время и размер файла.
    _call("publish_site", '{"body": "# Статус"}', user=user, db=db)
    published = _call("site_status", "{}", user=user, db=db)
    assert published["published"] is True
    assert published["published_at"] is not None
    assert published["file_exists"] is True
    assert published["file_bytes"] == len("# Статус".encode("utf-8"))

    # Смена домена = delete + create: публикация сброшена.
    _call("update_site", '{"domain": "statusy2"}', user=user, db=db)
    renamed = _call("site_status", "{}", user=user, db=db)
    assert renamed["domain"] == "statusy2.mzg.by"
    assert renamed["published"] is False
    assert renamed["file_exists"] is False

    # После удаления сайта снова нет.
    _call("delete_site", '{"confirm": true}', user=user, db=db)
    deleted = _call("site_status", "{}", user=user, db=db)
    assert deleted == {"exists": False}


def test_publish_site_tool_from_note(monkeypatch, client, db, tmp_path):
    _enable(monkeypatch, tmp_path)
    user, conv_id = _user_ctx(client, db)

    # Сайт не создан — ошибка.
    missing = _call("publish_site", "{}", user=user, db=db, conversation_id=conv_id)
    assert missing["error"] == SITE_NOT_FOUND

    _call("create_site", '{"domain": "notebook"}', user=user, db=db)

    # Заметки нет — предложение собрать страницу.
    empty = _call("publish_site", "{}", user=user, db=db, conversation_id=conv_id)
    assert "Заметка чата пуста" in empty["error"]

    client.put(f"/api/conversations/{conv_id}/note", json={"body": "# Привет"})
    published = _call("publish_site", "{}", user=user, db=db, conversation_id=conv_id)
    assert published["ok"] is True
    assert published["domain"] == "notebook.mzg.by"
    assert published["url"] == "https://notebook.mzg.by/"
    assert published["published"] is True
    assert (sites_folder() / "notebook.md").read_text(encoding="utf-8") == "# Привет"

    # Явный body важнее заметки; файл перезаписывается.
    explicit = _call("publish_site", '{"body": "# v2"}', user=user, db=db, conversation_id=conv_id)
    assert explicit["ok"] is True
    assert (sites_folder() / "notebook.md").read_text(encoding="utf-8") == "# v2"


def test_create_site_needs_user_context(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    result = _call("create_site", '{"domain": "nobody"}')
    assert "контекста" in result["error"]


# --- сквозной прогон через tool loop


def test_create_site_via_chat_loop(monkeypatch, client, db, tmp_path):
    _enable(monkeypatch, tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "")
    monkeypatch.setattr(settings, "feedback_webhook_url", "")
    monkeypatch.setattr(settings, "pzz_enabled", False)
    email = _email()
    register(client, email)
    user = get_user_by_email(db, email)
    conv = client.post("/api/conversations", json={"title": "s"}).json()
    conversation_id = str(conv["id"])

    async def weather(city, lat=None, lon=None):
        return '{"temperature_c": 0}'

    plan = StreamPlan(
        stream_responses=[
            _sse_tool_call("create_site", '{"domain": "loop1"}', "call_x"),
            _sse_text("Готово: loop1.mzg.by"),
        ],
        chat_responses=[],
    )
    _patch(monkeypatch, plan, weather)
    client.post("/login", data={"email": email, "password": "secret123"})
    response = client.post(
        "/api/chat",
        json={
            "stream": True,
            "messages": [{"role": "user", "content": "хочу сайт"}],
            "conversation_id": conversation_id,
        },
    )
    assert response.status_code == 200
    assert "loop1.mzg.by" in response.text
    db.expire_all()
    assert user_site(db, user).domain == "loop1"


def test_mzg_suffix_constant():
    assert MZG_SUFFIX == "mzg.by"
