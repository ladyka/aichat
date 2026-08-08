import uuid

from sqlalchemy import select

from app.db import ApiToken, ApiTokenUsage
from tests.conftest import create_token, register


def email():
    return f"api-{uuid.uuid4().hex[:8]}@example.com"


class FakeCompletionResponse:
    status_code = 200

    def json(self):
        return {
            "id": "cmpl-test",
            "object": "chat.completion",
            "model": "openrouter/free",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }


async def fake_chat_completions(payload):
    return FakeCompletionResponse()


async def fake_stream_completions(payload):
    chunk = (
        'data: {"choices":[{"delta":{"content":"Привет"}}],"model":"openrouter/free"}\n\n'
    ).encode("utf-8")
    yield chunk
    yield b"data: [DONE]\n\n"


def _patch_completions(monkeypatch):
    from app.routes import api as api_mod

    monkeypatch.setattr(api_mod, "chat_completions", fake_chat_completions)
    monkeypatch.setattr(api_mod, "stream_chat_completions", fake_stream_completions)


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_v1_models_requires_no_auth(client, mock_models):
    response = client.get("/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert "default" in [item["id"] for item in payload["data"]]


def test_api_models_requires_auth(client, mock_models):
    assert client.get("/api/models").status_code == 401
    register(client, email())
    assert client.get("/api/models").status_code == 200


def test_ui_chat_requires_auth(client):
    response = client.post("/api/chat", json={"model": "default", "messages": []})
    assert response.status_code == 401


def test_ui_chat_streams(client, mock_models, monkeypatch):
    register(client, email())
    _patch_completions(monkeypatch)
    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "Привет" in response.text


def test_v1_completions_auth(client, mock_models):
    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers("bogus"),
        json={"model": "default", "messages": []},
    )
    assert response.status_code == 401

    response = client.post(
        "/v1/chat/completions",
        json={"model": "default", "messages": []},
    )
    assert response.status_code == 401


def test_v1_completions_normalizes_model(client, mock_models, monkeypatch):
    register(client, email())
    token, _ = create_token(client)
    _patch_completions(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers(token),
        json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "default"


def test_daily_api_limit_per_token(client, mock_models, monkeypatch):
    register(client, email())
    token, _ = create_token(client)
    _patch_completions(monkeypatch)

    body = {"model": "default", "messages": [{"role": "user", "content": "hi"}]}
    for i in range(10):
        response = client.post("/v1/chat/completions", headers=_auth_headers(token), json=body)
        assert response.status_code == 200, f"request {i + 1} failed: {response.text}"

    response = client.post("/v1/chat/completions", headers=_auth_headers(token), json=body)
    assert response.status_code == 429
    assert "Дневной лимит API исчерпан" in response.json()["error"]
    assert response.headers["retry-after"] == "86400"


def test_api_limit_is_per_token(client, mock_models, monkeypatch):
    register(client, email())
    token_a, _ = create_token(client, name="a")
    token_b, _ = create_token(client, name="b")
    _patch_completions(monkeypatch)

    body = {"model": "default", "messages": [{"role": "user", "content": "hi"}]}
    for _ in range(10):
        client.post("/v1/chat/completions", headers=_auth_headers(token_a), json=body)

    assert (
        client.post("/v1/chat/completions", headers=_auth_headers(token_a), json=body).status_code
        == 429
    )
    assert (
        client.post("/v1/chat/completions", headers=_auth_headers(token_b), json=body).status_code
        == 200
    )


def test_chat_requests_do_not_count_against_api_limit(client, mock_models, monkeypatch, db):
    register(client, email())
    token, _ = create_token(client)
    _patch_completions(monkeypatch)

    body = {"model": "default", "messages": [{"role": "user", "content": "hi"}]}
    for _ in range(3):
        assert client.post("/api/chat", json={**body, "stream": True}).status_code == 200

    from app.auth import hash_token

    token_row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    assert token_row is not None
    usage = db.scalar(select(ApiTokenUsage).where(ApiTokenUsage.token_id == token_row.id))
    assert usage is None or usage.count == 0


def test_v1_streaming(client, mock_models, monkeypatch):
    register(client, email())
    token, _ = create_token(client)
    _patch_completions(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers(token),
        json={"model": "default", "messages": [], "stream": True},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "Привет" in response.text
    assert '"model": "default"' in response.text
