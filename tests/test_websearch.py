import json
import uuid

from app.config import Settings, get_settings
from app.db import UsageLog, get_user_by_email
from app.models_catalog import PROVIDER_E7_BY, PROVIDER_OPENROUTER
from app.telemetry import payload_for_trace
from app.tools import (
    attach_openrouter_web_search,
    enabled_tools,
    openrouter_web_search_tool,
    web_search_requests_from_usage,
    web_search_used_today,
)
from tests.conftest import register
from tests.test_tools import StreamPlan, _auth, _patch, _sse_text


def email():
    return f"websearch-{uuid.uuid4().hex[:8]}@example.com"


def _enable_web_search(monkeypatch, *, daily_limit=20, engine="exa"):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(settings, "openrouter_web_search", True)
    monkeypatch.setattr(settings, "openrouter_web_search_engine", engine)
    monkeypatch.setattr(settings, "openrouter_web_search_max_results", 5)
    monkeypatch.setattr(settings, "openrouter_web_search_max_uses", 3)
    monkeypatch.setattr(settings, "openrouter_web_search_daily_limit", daily_limit)
    monkeypatch.setattr(settings, "pzz_enabled", False)
    monkeypatch.setattr(settings, "openweather_api_key", "")
    monkeypatch.setattr(settings, "feedback_webhook_url", "")


def _server_tool(payload):
    return [
        tool
        for tool in payload.get("tools") or []
        if isinstance(tool, dict) and tool.get("type") == "openrouter:web_search"
    ]


def test_enabled_tools_does_not_include_openrouter_web_search(monkeypatch):
    _enable_web_search(monkeypatch)
    names = [t["function"]["name"] for t in enabled_tools()]
    assert "web_search" not in names
    assert all(t.get("type") == "function" for t in enabled_tools())


def test_openrouter_web_search_tool_pins_engine():
    tool = openrouter_web_search_tool()
    assert tool["type"] == "openrouter:web_search"
    assert tool["parameters"]["engine"] in {"exa", "parallel", "perplexity", "firecrawl"}
    assert tool["parameters"]["engine"] != "auto"
    assert tool["parameters"]["engine"] != "native"


def test_web_search_requests_from_usage():
    assert web_search_requests_from_usage(None) == 0
    assert web_search_requests_from_usage({"server_tool_use": {"web_search_requests": 2}}) == 2
    assert web_search_requests_from_usage({"server_tool_use": {"web_search_requests": "x"}}) == 0


def test_attach_skips_non_openrouter(monkeypatch):
    _enable_web_search(monkeypatch)
    payload = {"tools": [{"type": "function", "function": {"name": "x"}}]}
    out = attach_openrouter_web_search(payload, provider=PROVIDER_E7_BY, user=None, db=None)
    assert _server_tool(out) == []


def test_attach_skips_when_disabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_web_search", False)
    payload = {"tools": [{"type": "function", "function": {"name": "x"}}]}
    out = attach_openrouter_web_search(payload, provider=PROVIDER_OPENROUTER, user=None, db=None)
    assert _server_tool(out) == []


def test_chat_advertises_openrouter_web_search(client, mock_models, monkeypatch):
    _enable_web_search(monkeypatch)
    _auth(client)
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
    tools = _server_tool(plan.stream_payloads[0])
    assert len(tools) == 1
    assert tools[0]["parameters"]["engine"] == "exa"
    assert tools[0]["parameters"]["max_results"] == 5
    assert tools[0]["parameters"]["max_uses"] == 3


def test_v1_does_not_inject_openrouter_web_search(client, mock_models, monkeypatch, api_key):
    from tests.conftest import create_token
    from tests.test_tools import FakeChatResponse

    _enable_web_search(monkeypatch)
    _auth(client)
    token, _ = create_token(client, "websearch-proxy")
    plan = StreamPlan(
        stream_responses=[],
        chat_responses=[FakeChatResponse({"role": "assistant", "content": "ok"})],
    )
    _patch(monkeypatch, plan, lambda *a, **k: "{}")
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert "tools" not in plan.chat_payloads[0]


def test_daily_limit_skips_web_search(client, mock_models, monkeypatch, db):
    _enable_web_search(monkeypatch, daily_limit=2)
    address = email()
    register(client, address)
    user = get_user_by_email(db, address)
    db.add(
        UsageLog(
            user_id=user.id,
            model="default",
            prompt_tokens=1,
            completion_tokens=1,
            source="chat",
            detail=json.dumps({"web_search_requests": 2}, ensure_ascii=False),
        )
    )
    db.commit()
    assert web_search_used_today(db, user.id) == 2

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
    assert _server_tool(plan.stream_payloads[0]) == []


def test_stream_logs_web_search_usage(client, mock_models, monkeypatch, db):
    _enable_web_search(monkeypatch)
    address = email()
    register(client, address)
    usage_event = (
        "data: "
        + json.dumps(
            {
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "server_tool_use": {"web_search_requests": 2},
                }
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )
    plan = StreamPlan(stream_responses=[[_sse_text("Ответ со ссылками"), usage_event.encode()]])
    _patch(monkeypatch, plan, lambda *a, **k: "{}")
    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "что в новостях"}],
        },
    )
    assert response.status_code == 200
    user = get_user_by_email(db, address)
    db.expire_all()
    assert web_search_used_today(db, user.id) == 2


def test_payload_for_trace_includes_openrouter_web_search():
    traced = payload_for_trace(
        {
            "model": "default",
            "tools": [
                {"type": "function", "function": {"name": "download_file"}},
                {"type": "openrouter:web_search", "parameters": {"engine": "exa"}},
            ],
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert traced["tools"] == ["download_file", "openrouter:web_search"]


def test_settings_invalid_engine_falls_back_to_exa(monkeypatch):
    monkeypatch.setenv("OPENROUTER_WEB_SEARCH_ENGINE", "bing")
    monkeypatch.setenv("OPENROUTER_WEB_SEARCH_MAX_RESULTS", "99")
    monkeypatch.setenv("OPENROUTER_WEB_SEARCH_MAX_USES", "0")
    settings = Settings()
    assert settings.openrouter_web_search_engine == "exa"
    assert settings.openrouter_web_search_max_results == 25
    assert settings.openrouter_web_search_max_uses == 1
