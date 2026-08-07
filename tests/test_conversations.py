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
        json={"messages": [{"role": "user", "content": "Первый вопрос"}, {"role": "assistant", "content": "Ответ"}]},
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
    assert client.patch(f"/api/conversations/{conv['id']}", json={"archived": True}).status_code == 404


def test_settings_get_and_put(client, mock_models):
    _auth(client)
    assert client.get("/api/settings").json() == {"preferred_model": "default"}

    response = client.put("/api/settings", json={"preferred_model": "default"})
    assert response.status_code == 200
    assert response.json() == {"preferred_model": "default"}
