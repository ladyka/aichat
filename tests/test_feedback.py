import asyncio
import json
import uuid
from types import SimpleNamespace

from app.config import get_settings
from app.db import get_user_by_email
from app.feedback import format_feedback_text
from app.tools import call_tool, enabled_tools
from tests.conftest import register
from tests.test_tools import (
    FakeChatResponse,
    StreamPlan,
    _auth,
    _patch,
    _sse_text,
    _tool_message,
)


def _email():
    return f"fb-{uuid.uuid4().hex[:8]}@example.com"


class FakeWebhookResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeWebhookClient:
    def __init__(self, *a, **kw):
        self.posts = []
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.posts.append({"url": url, "json": json})
        return FakeWebhookResponse(self.status_code)


def _user(email="person@example.com", user_id=7):
    return SimpleNamespace(id=user_id, email=email)


def _enable(monkeypatch, url="https://hooks.slack.com/services/T/B/xxx"):
    monkeypatch.setattr(get_settings(), "feedback_webhook_url", url)


def _call(arguments, user=None, db=None, conversation_id=None):
    return json.loads(
        asyncio.run(
            call_tool(
                "send_feedback",
                arguments if isinstance(arguments, str) else json.dumps(arguments),
                user=user,
                db=db,
                conversation_id=conversation_id,
            )
        )
    )


def test_send_feedback_not_advertised_without_webhook(monkeypatch):
    monkeypatch.setattr(get_settings(), "feedback_webhook_url", "")
    names = [t["function"]["name"] for t in enabled_tools()]
    assert "send_feedback" not in names


def test_send_feedback_advertised_with_webhook(monkeypatch):
    _enable(monkeypatch)
    names = [t["function"]["name"] for t in enabled_tools()]
    assert names[-1] == "send_feedback"


def test_send_feedback_not_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "feedback_webhook_url", "")
    result = _call({"category": "idea", "message": "тема"}, user=_user())
    assert "не настроена" in result["error"]


def test_send_feedback_needs_user(monkeypatch):
    _enable(monkeypatch)
    result = _call({"category": "idea", "message": "тема"})
    assert "пользователя" in result["error"]


def test_send_feedback_rejects_bad_category(monkeypatch):
    _enable(monkeypatch)
    result = _call({"category": "spam", "message": "тема"}, user=_user())
    assert "category" in result["error"]


def test_send_feedback_requires_message(monkeypatch):
    _enable(monkeypatch)
    result = _call({"category": "idea"}, user=_user())
    assert "message" in result["error"]


def test_send_feedback_complaint_needs_objection(monkeypatch):
    _enable(monkeypatch)
    result = _call(
        {"category": "complaint", "message": "плохо"},
        user=_user(),
    )
    assert result["needs_objection"] is True
    assert "objection" in result["error"]


def test_send_feedback_preview_without_confirm(monkeypatch):
    _enable(monkeypatch)
    result = _call(
        {
            "category": "complaint",
            "message": "Ответ про погоду был пустой",
            "objection": "get_weather вернул ошибку, хотя город Минск",
        },
        user=_user(),
    )
    assert result["preview"] is True
    assert result["category"] == "complaint"
    assert "get_weather" in result["objection"]
    assert "confirm=true" in result["hint"]


def test_send_feedback_posts_slack_text(monkeypatch):
    _enable(monkeypatch)
    client = FakeWebhookClient()
    monkeypatch.setattr("app.feedback.httpx.AsyncClient", lambda *a, **kw: client)
    result = _call(
        {
            "category": "idea",
            "message": "Добавьте поиск по навыкам",
            "confirm": True,
        },
        user=_user(),
    )
    assert result["ok"] is True
    assert len(client.posts) == 1
    body = client.posts[0]["json"]
    assert "text" in body
    assert "content" not in body
    assert "Идея от person@example.com" in body["text"]
    assert "Добавьте поиск по навыкам" in body["text"]
    assert client.posts[0]["url"].startswith("https://hooks.slack.com/")


def test_send_feedback_posts_discord_content(monkeypatch):
    _enable(monkeypatch, url="https://discord.com/api/webhooks/1/token")
    client = FakeWebhookClient()
    monkeypatch.setattr("app.feedback.httpx.AsyncClient", lambda *a, **kw: client)
    result = _call(
        {"category": "question", "message": "Когда будет диктофон?", "confirm": True},
        user=_user(),
    )
    assert result["ok"] is True
    body = client.posts[0]["json"]
    assert "content" in body
    assert "text" not in body
    assert "Вопрос" in body["content"]


def test_send_feedback_webhook_http_error(monkeypatch):
    _enable(monkeypatch)
    client = FakeWebhookClient()
    client.status_code = 500
    monkeypatch.setattr("app.feedback.httpx.AsyncClient", lambda *a, **kw: client)
    result = _call(
        {"category": "idea", "message": "тема", "confirm": True},
        user=_user(),
    )
    assert "Не удалось" in result["error"]


def test_send_feedback_includes_conversation_title(monkeypatch, client, db):
    _enable(monkeypatch)
    addr = _email()
    register(client, addr)
    conv = client.post("/api/conversations", json={"title": "Сломанная погода"}).json()
    user = get_user_by_email(db, addr)
    hook = FakeWebhookClient()
    monkeypatch.setattr("app.feedback.httpx.AsyncClient", lambda *a, **kw: hook)
    result = _call(
        {
            "category": "complaint",
            "message": "Погода молчит",
            "objection": "get_weather не ответил по Минску",
            "confirm": True,
        },
        user=user,
        db=db,
        conversation_id=conv["id"],
    )
    assert result["ok"] is True
    text = hook.posts[0]["json"]["text"]
    assert f"Чат {conv['id']}" in text
    assert "Сломанная погода" in text
    assert "get_weather не ответил" in text


def test_format_feedback_text_skips_empty_objection():
    text = format_feedback_text(
        category="idea",
        message="поиск",
        objection="",
        user_email="a@b.c",
        user_id=1,
        conversation_id="",
        conversation_title="",
    )
    assert "Что не понравилось" not in text
    assert text.startswith("Идея от a@b.c")


def test_send_feedback_chat_loop(client, mock_models, monkeypatch):
    _enable(monkeypatch)
    _auth(client)
    hook = FakeWebhookClient()
    monkeypatch.setattr("app.feedback.httpx.AsyncClient", lambda *a, **kw: hook)
    args = json.dumps(
        {
            "category": "complaint",
            "message": "Картинка не нарисовалась",
            "objection": "generate_image вернул ошибку S3",
            "confirm": True,
        },
        ensure_ascii=False,
    )
    plan = StreamPlan(
        stream_responses=[],
        chat_responses=[
            FakeChatResponse(_tool_message("send_feedback", args, "call_fb")),
            FakeChatResponse({"role": "assistant", "content": "Передал команде."}),
        ],
    )
    _patch(monkeypatch, plan, lambda *a, **k: "{}")

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": False,
            "messages": [{"role": "user", "content": "Передайте команде: картинка сломалась"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Передал команде."
    assert hook.posts
    tool_msg = plan.chat_payloads[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["content"])["ok"] is True


def test_send_feedback_advertised_in_chat(client, mock_models, monkeypatch):
    _enable(monkeypatch)
    _auth(client)
    monkeypatch.setattr(get_settings(), "pzz_enabled", False)
    monkeypatch.setattr(get_settings(), "openweather_api_key", "")
    plan = StreamPlan(stream_responses=[_sse_text("Привет")])
    _patch(monkeypatch, plan, lambda *a, **k: "{}")
    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    tools = [t["function"]["name"] for t in plan.stream_payloads[0]["tools"]]
    assert "send_feedback" in tools
