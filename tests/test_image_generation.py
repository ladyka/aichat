import asyncio
import base64
import json
import logging
import uuid

from sqlalchemy import func, select

from app.config import get_settings
from app.db import GeneratedImage, get_user_by_email
from app.tools import call_tool, enabled_tools
from tests.conftest import register
from tests.test_tools import (
    FakeChatResponse,
    StreamPlan,
    _auth,
    _patch,
    _sse_text,
    _sse_tool_call,
)

_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)


def _enable_image_gen(monkeypatch, *, daily_limit=5):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(settings, "s3_endpoint", "https://s3.cloud.ru")
    monkeypatch.setattr(settings, "s3_bucket", "aichat")
    monkeypatch.setattr(settings, "s3_path_style", True)
    monkeypatch.setattr(settings, "s3_public_base_url", "https://aichat.s3.cloud.ru")
    monkeypatch.setattr(settings, "s3_sa_key_id", "key")
    monkeypatch.setattr(settings, "s3_sa_key_secret", "secret")
    monkeypatch.setattr(settings, "image_generation_daily_limit", daily_limit)
    monkeypatch.setattr(settings, "image_generation_model", "black-forest-labs/flux.2-klein-4b")
    monkeypatch.setattr(settings, "debug", False)


def test_generate_image_not_advertised_without_s3(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(settings, "s3_bucket", "")
    monkeypatch.setattr(settings, "s3_sa_key_id", "")
    names = [t["function"]["name"] for t in enabled_tools()]
    assert "generate_image" not in names


def test_generate_image_advertised_when_configured(monkeypatch):
    _enable_image_gen(monkeypatch)
    names = [t["function"]["name"] for t in enabled_tools()]
    assert "generate_image" in names


def test_generate_image_tool_uploads(monkeypatch, client, db, caplog):
    _enable_image_gen(monkeypatch)
    email = f"img-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)

    async def fake_or(prompt, aspect_ratio=None, model=None):
        assert "гора" in prompt
        assert aspect_ratio == "16:9"
        return {
            "bytes": base64.b64decode(_PNG_B64),
            "media_type": "image/png",
            "cost": 0.014,
            "model": "black-forest-labs/flux.2-klein-4b",
        }

    def fake_put(key, body, content_type):
        assert key.startswith(f"aichat/generated/{user.id}/")
        assert content_type == "image/png"
        assert body
        return f"https://aichat.s3.cloud.ru/{key}"

    monkeypatch.setattr("app.tools.openrouter_generate_image", fake_or)
    monkeypatch.setattr("app.tools.storage.put_bytes", fake_put)

    with caplog.at_level(logging.INFO, logger="aichat.tools"):
        raw = asyncio.run(
            call_tool(
                "generate_image",
                json.dumps({"prompt": "гора на закате", "aspect_ratio": "16:9"}),
                user=user,
                db=db,
            )
        )
    payload = json.loads(raw)
    assert payload["markdown"].startswith("![")
    assert payload["url"].startswith("https://aichat.s3.cloud.ru/")
    assert payload["cost_usd"] == "0.014"
    assert "generate_image started" in caplog.text
    assert "generate_image saved" in caplog.text
    assert payload["url"] not in caplog.text
    row = db.scalar(select(GeneratedImage).where(GeneratedImage.user_id == user.id))
    assert row.prompt == "гора на закате"
    assert row.aspect_ratio == "16:9"


def test_generate_image_daily_limit(monkeypatch, client, db):
    _enable_image_gen(monkeypatch, daily_limit=1)
    email = f"img-lim-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)

    async def fake_or(prompt, aspect_ratio=None, model=None):
        return {
            "bytes": base64.b64decode(_PNG_B64),
            "media_type": "image/png",
            "cost": 0.01,
            "model": "black-forest-labs/flux.2-klein-4b",
        }

    monkeypatch.setattr("app.tools.openrouter_generate_image", fake_or)
    monkeypatch.setattr(
        "app.tools.storage.put_bytes",
        lambda key, body, content_type: f"https://aichat.s3.cloud.ru/{key}",
    )

    first = json.loads(
        asyncio.run(call_tool("generate_image", '{"prompt": "кот"}', user=user, db=db))
    )
    assert "url" in first
    second = json.loads(
        asyncio.run(call_tool("generate_image", '{"prompt": "пёс"}', user=user, db=db))
    )
    assert "лимит" in second["error"]


def test_generate_image_openrouter_error(monkeypatch, client, db, caplog):
    _enable_image_gen(monkeypatch)
    email = f"img-err-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)

    async def fake_or(prompt, aspect_ratio=None, model=None):
        return {"error": "Insufficient credits", "status": 402}

    monkeypatch.setattr("app.tools.openrouter_generate_image", fake_or)
    with caplog.at_level(logging.WARNING, logger="aichat.tools"):
        result = json.loads(
            asyncio.run(call_tool("generate_image", '{"prompt": "кот"}', user=user, db=db))
        )
    assert result["error"] == "Insufficient credits"
    leftover = db.scalar(
        select(func.count()).select_from(GeneratedImage).where(GeneratedImage.user_id == user.id)
    )
    assert int(leftover or 0) == 0
    assert "generate_image failed" in caplog.text
    assert "Insufficient credits" in caplog.text


def test_generate_image_logs_url_at_debug(monkeypatch, client, db, caplog):
    _enable_image_gen(monkeypatch)
    email = f"img-dbg-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)

    async def fake_or(prompt, aspect_ratio=None, model=None):
        return {
            "bytes": base64.b64decode(_PNG_B64),
            "media_type": "image/png",
            "cost": 0.01,
            "model": "black-forest-labs/flux.2-klein-4b",
        }

    monkeypatch.setattr("app.tools.openrouter_generate_image", fake_or)
    monkeypatch.setattr(
        "app.tools.storage.put_bytes",
        lambda key, body, content_type: f"https://aichat.s3.cloud.ru/{key}",
    )
    with caplog.at_level(logging.DEBUG, logger="aichat.tools"):
        raw = asyncio.run(call_tool("generate_image", '{"prompt": "кот"}', user=user, db=db))
    payload = json.loads(raw)
    debug_text = "\n".join(
        record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG
    )
    assert f"generate_image url={payload['url']}" in debug_text


def test_generate_image_logs_s3_failure(monkeypatch, client, db, caplog):
    _enable_image_gen(monkeypatch)
    email = f"img-s3-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)

    async def fake_or(prompt, aspect_ratio=None, model=None):
        return {
            "bytes": base64.b64decode(_PNG_B64),
            "media_type": "image/png",
            "cost": 0.01,
            "model": "black-forest-labs/flux.2-klein-4b",
        }

    def boom(key, body, content_type):
        raise RuntimeError("access denied")

    monkeypatch.setattr("app.tools.openrouter_generate_image", fake_or)
    monkeypatch.setattr("app.tools.storage.put_bytes", boom)
    with caplog.at_level(logging.WARNING, logger="aichat.tools"):
        result = json.loads(
            asyncio.run(call_tool("generate_image", '{"prompt": "кот"}', user=user, db=db))
        )
    assert "Не удалось сохранить" in result["error"]
    assert "generate_image failed" in caplog.text
    assert "stage=s3" in caplog.text


def test_generate_image_saves_local_copy_when_debug(monkeypatch, client, db, tmp_path):
    _enable_image_gen(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "root", tmp_path)
    email = f"img-dbg-disk-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)
    png = base64.b64decode(_PNG_B64)

    async def fake_or(prompt, aspect_ratio=None, model=None):
        return {
            "bytes": png,
            "media_type": "image/png",
            "cost": 0.01,
            "model": "black-forest-labs/flux.2-klein-4b",
        }

    monkeypatch.setattr("app.tools.openrouter_generate_image", fake_or)
    monkeypatch.setattr(
        "app.tools.storage.put_bytes",
        lambda key, body, content_type: f"https://aichat.s3.cloud.ru/{key}",
    )
    asyncio.run(call_tool("generate_image", '{"prompt": "кот"}', user=user, db=db))
    files = list((tmp_path / "data" / "aichat" / "generated" / str(user.id)).rglob("*.png"))
    assert len(files) == 1
    assert files[0].read_bytes() == png


def _fake_weather(*a, **kw):
    return json.dumps({"ok": True})


def test_chat_generate_image_tool_loop(client, mock_models, monkeypatch, db):
    _enable_image_gen(monkeypatch)
    _auth(client)

    async def fake_or(prompt, aspect_ratio=None, model=None):
        return {
            "bytes": base64.b64decode(_PNG_B64),
            "media_type": "image/png",
            "cost": 0.02,
            "model": "black-forest-labs/flux.2-klein-4b",
        }

    monkeypatch.setattr("app.tools.openrouter_generate_image", fake_or)
    monkeypatch.setattr(
        "app.tools.storage.put_bytes",
        lambda key, body, content_type: f"https://aichat.s3.cloud.ru/{key}",
    )

    plan = StreamPlan(
        stream_responses=[
            _sse_tool_call("generate_image", '{"prompt": "закат"}'),
            [_sse_text("Вот картинка ![закат](https://aichat.s3.cloud.ru/x.png)")],
        ]
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "нарисуй закат"}],
        },
    )
    assert response.status_code == 200
    tools = [t["function"]["name"] for t in plan.stream_payloads[0]["tools"]]
    assert "generate_image" in tools
    assert b"https://aichat.s3.cloud.ru" in response.content


def test_v1_does_not_inject_generate_image(client, mock_models, monkeypatch, api_key):
    from tests.conftest import create_token

    _enable_image_gen(monkeypatch)
    _auth(client)
    token, _ = create_token(client, "img-proxy")
    plan = StreamPlan(
        stream_responses=[],
        chat_responses=[FakeChatResponse({"role": "assistant", "content": "ok"})],
    )
    _patch(monkeypatch, plan, _fake_weather)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert "tools" not in plan.chat_payloads[0]
