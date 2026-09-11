import uuid

from tests.conftest import register


def email():
    return f"conv-{uuid.uuid4().hex[:8]}@example.com"


def _auth(client):
    register(client, email())


def _create_conversation(client, title=None):
    body = {}
    if title is not None:
        body["title"] = title
    return client.post("/api/conversations", json=body)


def test_requires_auth(client):
    assert client.get("/api/conversations").status_code == 401
    assert client.post("/api/conversations", json={}).status_code == 401
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings", json={"preferred_model": "default"}).status_code == 401


def test_conversations_crud(client):
    _auth(client)

    empty = client.get("/api/conversations")
    assert empty.status_code == 200
    assert empty.json()["data"] == []

    created = _create_conversation(client, title="Мой чат")
    assert created.status_code == 200
    conv = created.json()
    assert conv["title"] == "Мой чат"

    listed = client.get("/api/conversations")
    assert len(listed.json()["data"]) == 1

    fetched = client.get(f"/api/conversations/{conv['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["messages"] == []

    patched = client.patch(
        f"/api/conversations/{conv['id']}",
        json={"title": "Новое имя", "archived": True},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Новое имя"
    assert patched.json()["archived_at"] is not None

    after_archive = client.get("/api/conversations")
    assert after_archive.json()["data"] == []

    unarchived = client.patch(
        f"/api/conversations/{conv['id']}",
        json={"archived": False},
    )
    assert unarchived.json()["archived_at"] is None

    deleted = client.delete(f"/api/conversations/{conv['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True

    assert client.get(f"/api/conversations/{conv['id']}").status_code == 404


def test_append_messages_sets_title(client):
    _auth(client)
    conv = _create_conversation(client).json()
    response = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={
            "messages": [
                {"role": "user", "content": "Первый вопрос"},
                {"role": "assistant", "content": "Ответ"},
            ]
        },
    )
    assert response.status_code == 200
    assert len(response.json()["data"]) == 2

    fetched = client.get(f"/api/conversations/{conv['id']}")
    assert fetched.json()["title"] == "Первый вопрос"


def test_append_messages_invalid_role(client):
    _auth(client)
    conv = _create_conversation(client).json()
    response = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"messages": [{"role": "admin", "content": "x"}]},
    )
    assert response.status_code == 400


def test_conversations_are_owned(client):
    _auth(client)
    conv = _create_conversation(client).json()

    # a second user cannot see/delete the first user's conversation
    register(client, email())
    assert client.get(f"/api/conversations/{conv['id']}").status_code == 404
    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 404
    assert (
        client.patch(f"/api/conversations/{conv['id']}", json={"archived": True}).status_code == 404
    )


def test_settings_get_and_put(client, mock_models):
    _auth(client)
    assert client.get("/api/settings").json() == {
        "preferred_model": "default",
        "default_skill_ids": [],
    }

    response = client.put("/api/settings", json={"preferred_model": "default"})
    assert response.status_code == 200
    assert response.json() == {"preferred_model": "default", "default_skill_ids": []}


def test_title_regenerated_at_checkpoints(client, monkeypatch):
    from app import title as title_mod

    calls: list[str] = []

    async def fake_request_title(transcript):
        calls.append(transcript)
        return "Тема диалога"

    monkeypatch.setattr(title_mod, "_request_title", fake_request_title)
    _auth(client)
    conv = client.post("/api/conversations", json={"title": "Чат"}).json()

    def append(role, content):
        return client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"messages": [{"role": role, "content": content}]},
        )

    append("user", "вопрос про погоду")
    append("assistant", "ответ 1")  # checkpoint 1
    assert len(calls) == 1
    append("assistant", "ответ 2")  # checkpoint 2
    assert len(calls) == 2
    append("assistant", "ответ 3")  # не checkpoint
    append("assistant", "ответ 4")
    assert len(calls) == 2
    append("assistant", "ответ 5")  # checkpoint 5
    assert len(calls) == 3

    fetched = client.get(f"/api/conversations/{conv['id']}").json()
    assert fetched["title"] == "Тема диалога"


def test_title_task_skips_when_no_messages(client):
    from app.title import _build_transcript

    assert _build_transcript([]) == ""


def test_truncate_title_strips_leaked_thinking():
    # glm-5.3-flash игнорирует think=false и дописывает рассуждения перед </think>.
    from app.title import THINK_MARKER, _truncate_title

    leaked = (
        "We need a title. Based on the dialogue: aichat.by.\n\n"
        + THINK_MARKER
        + "Возможности ассистента"
    )
    assert _truncate_title(leaked) == "Возможности ассистента"
    # Модель оборвала монолог, не дойдя до заголовка — название не портим.
    assert (
        _truncate_title("We need to pick one of the options below for this particular chat") == ""
    )
    assert _truncate_title("Тема диалога.") == "Тема диалога"
    assert _truncate_title("  «Погода в Минске»  ") == "Погода в Минске"
    assert _truncate_title("") == ""


def test_request_title_disables_thinking_for_ol(client, monkeypatch):
    import asyncio

    from app import title as title_mod
    from app.models_catalog import ModelRoute

    captured: list[dict] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "Тема"}}]}

    async def fake_call_llm(route, payload):
        captured.append(payload)
        return FakeResponse()

    monkeypatch.setattr(title_mod, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        title_mod,
        "resolve_model",
        lambda _model: ModelRoute(
            public_id="ol/deepseek-v4.1-flash",
            provider="ol",
            upstream_id="deepseek-v4.1-flash:cloud",
        ),
    )
    assert asyncio.run(title_mod._request_title("user: привет")) == "Тема"
    assert captured[0]["think"] is False
    assert captured[0]["max_tokens"] == title_mod.TITLE_MAX_TOKENS


def test_manual_rename_locks_title(client, monkeypatch):
    from app import title as title_mod

    calls: list[str] = []

    async def fake_request_title(transcript):
        calls.append(transcript)
        return "Авто-тема"

    monkeypatch.setattr(title_mod, "_request_title", fake_request_title)
    _auth(client)
    conv = client.post("/api/conversations", json={"title": "Новый чат"}).json()
    conv_id = conv["id"]

    client.post(
        f"/api/conversations/{conv_id}/messages",
        json={
            "messages": [
                {"role": "user", "content": "вопрос"},
                {"role": "assistant", "content": "ответ"},
            ]
        },
    )
    fetched = client.get(f"/api/conversations/{conv_id}").json()
    assert fetched["title"] == "Авто-тема"

    renamed = client.patch(f"/api/conversations/{conv_id}", json={"title": "Моё название"}).json()
    assert renamed["title"] == "Моё название"

    client.post(
        f"/api/conversations/{conv_id}/messages",
        json={
            "messages": [
                {"role": "user", "content": "ещё вопрос"},
                {"role": "assistant", "content": "ещё ответ"},
            ]
        },
    )
    fetched = client.get(f"/api/conversations/{conv_id}").json()
    assert fetched["title"] == "Моё название"


def test_patch_title_to_same_value_does_not_lock(client):
    _auth(client)
    conv = client.post("/api/conversations", json={"title": "Новый чат"}).json()
    patched = client.patch(f"/api/conversations/{conv['id']}", json={"title": "Новый чат"}).json()
    assert patched["title"] == "Новый чат"
