import asyncio
import json
import uuid

import pytest

from app.config import get_settings
from app.tools import call_tool, enabled_tools, extract_tool_calls
from tests.conftest import register


def email():
    return f"tools-{uuid.uuid4().hex[:8]}@example.com"


def _auth(client):
    register(client, email())


def _sse_text(text, model="openrouter/free"):
    data = {"choices": [{"delta": {"content": text}}], "model": model}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _sse_tool_call(name, args, call_id="call_1", index=0):
    def event(delta):
        data = {"choices": [{"delta": {"tool_calls": [delta]}}]}
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

    return [
        event(
            {
                "index": index,
                "id": call_id,
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
        ),
        event({"index": index, "function": {"name": name, "arguments": ""}}),
        event({"index": index, "function": {"name": "", "arguments": args}}),
        b"data: [DONE]\n\n",
    ]


class FakeChatResponse:
    def __init__(self, message, status_code=200, model="openrouter/free"):
        self.status_code = status_code
        self._message = message
        self._model = model

    def json(self):
        return {
            "id": "cmpl-test",
            "object": "chat.completion",
            "model": self._model,
            "choices": [{"index": 0, "message": self._message}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }


class StreamPlan:
    """Stateful fake for stream_chat_completions + chat_completions."""

    def __init__(self, stream_responses, chat_responses=None):
        self.stream_responses = list(stream_responses)
        self.chat_responses = list(chat_responses or [])
        self.stream_payloads = []
        self.chat_payloads = []
        self.stream_calls = 0
        self.chat_calls = 0

    async def stream(self, payload):
        self.stream_calls += 1
        self.stream_payloads.append(payload)
        if self.stream_responses:
            chunks = self.stream_responses.pop(0)
            if isinstance(chunks, (bytes, str)):
                chunks = [chunks]
            for chunk in chunks:
                yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk

    async def chat(self, payload):
        self.chat_calls += 1
        self.chat_payloads.append(payload)
        return self.chat_responses.pop(0)


def _patch(monkeypatch, plan, weather):
    from app.routes import api as api_mod

    monkeypatch.setattr(api_mod, "stream_chat_completions", plan.stream)
    monkeypatch.setattr(api_mod, "chat_completions", plan.chat)
    monkeypatch.setattr("app.tools._weather", weather)


def _tool_message(name="get_weather", arguments='{"city": "Minsk"}', call_id="call_1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


def test_enabled_tools_without_key(monkeypatch):
    settings = get_settings()
    settings.openweather_api_key = ""
    monkeypatch.setattr(settings, "pzz_enabled", True)
    assert [t["function"]["name"] for t in enabled_tools()] == [
        "get_current_datetime",
        "download_file",
        "pravo_search",
        "pravo_get_document",
        "pzz_search_menu",
        "pzz_lookup_address",
        "pzz_place_order",
    ]


def test_enabled_tools_with_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    monkeypatch.setattr(settings, "pzz_enabled", True)
    tools = enabled_tools()
    assert [t["function"]["name"] for t in tools] == [
        "get_current_datetime",
        "download_file",
        "pravo_search",
        "pravo_get_document",
        "pzz_search_menu",
        "pzz_lookup_address",
        "pzz_place_order",
        "get_weather",
        "get_user_location",
    ]


def test_extract_tool_calls_aggregates():
    raw = b"".join(_sse_tool_call("get_weather", '{"city": "Minsk"}', "call_1"))
    calls = extract_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert calls[0]["name"] == "get_weather"
    assert json.loads(calls[0]["arguments"]) == {"city": "Minsk"}


def test_extract_tool_calls_multiple():
    raw = b"".join(
        _sse_tool_call("get_weather", "{}", "call_a", index=0)
        + _sse_tool_call("get_weather", '{"city": "Rome"}', "call_b", index=1)
    )
    calls = extract_tool_calls(raw)
    assert [c["id"] for c in calls] == ["call_a", "call_b"]
    assert json.loads(calls[1]["arguments"]) == {"city": "Rome"}


def test_call_tool_unknown():
    import asyncio

    result = json.loads(asyncio.run(call_tool("bogus", "{}")))
    assert result["error"]


def test_call_tool_current_datetime():
    import asyncio

    result = json.loads(asyncio.run(call_tool("get_current_datetime", "{}")))
    assert result["date"]
    assert result["time"]
    assert result["weekday"]
    assert result["timezone"]
    assert len(result["date"]) == 10
    assert len(result["time"]) == 8


def test_call_tool_current_datetime_with_timezone():
    import asyncio

    result = json.loads(
        asyncio.run(call_tool("get_current_datetime", '{"timezone": "Europe/Minsk"}'))
    )
    assert result["timezone"] == "Europe/Minsk"
    assert result["date"]
    assert result["time"]


def test_call_tool_current_datetime_bad_timezone():
    import asyncio

    result = json.loads(
        asyncio.run(call_tool("get_current_datetime", '{"timezone": "No/SuchZone"}'))
    )
    assert "часовой пояс" in result["error"]


def test_call_tool_current_datetime_invalid_json():
    import asyncio

    result = json.loads(asyncio.run(call_tool("get_current_datetime", "not-json")))
    assert result["date"]
    assert result["time"]


class FakeWeatherResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeWeatherClient:
    def __init__(self, *a, **kw):
        self.get_kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **kw):
        self.get_kwargs = kw
        return FakeWeatherResponse(
            status_code=200,
            data={
                "cod": 200,
                "name": "Minsk",
                "sys": {"country": "BY"},
                "main": {"temp": 15.0, "feels_like": 14.0, "humidity": 60},
                "wind": {"speed": 3.1},
                "weather": [{"description": "ясно"}],
            },
        )


def test_weather_success(monkeypatch):
    import asyncio

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openweather_api_key", "ow-test")
    client = FakeWeatherClient()
    monkeypatch.setattr("app.tools.httpx.AsyncClient", lambda *a, **kw: client)

    result = json.loads(asyncio.run(call_tool("get_weather", '{"city": "Minsk"}')))
    assert result["city"] == "Minsk, BY"
    assert result["temperature_c"] == 15.0
    assert client.get_kwargs["params"]["units"] == "metric"
    assert client.get_kwargs["params"]["lang"] == "ru"


def test_weather_not_found(monkeypatch):
    import asyncio

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openweather_api_key", "ow-test")

    class NotFoundClient(FakeWeatherClient):
        async def get(self, *a, **kw):
            return FakeWeatherResponse(
                status_code=404, data={"cod": "404", "message": "city not found"}
            )

    monkeypatch.setattr("app.tools.httpx.AsyncClient", NotFoundClient)

    result = json.loads(asyncio.run(call_tool("get_weather", '{"city": "Atlantis"}')))
    assert "city not found" in result["error"]


def test_weather_no_key(monkeypatch):
    import asyncio

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openweather_api_key", "")
    result = json.loads(asyncio.run(call_tool("get_weather", '{"city": "Minsk"}')))
    assert "не настроен" in result["error"]


def test_weather_empty_city(monkeypatch):
    import asyncio

    result = json.loads(asyncio.run(call_tool("get_weather", "{}")))
    assert "город" in result["error"]
    assert "get_user_location" in result["error"]


def test_weather_by_coords(monkeypatch):
    import asyncio

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openweather_api_key", "ow-test")
    client = FakeWeatherClient()
    monkeypatch.setattr("app.tools.httpx.AsyncClient", lambda *a, **kw: client)

    result = json.loads(asyncio.run(call_tool("get_weather", '{"lat": 53.9, "lon": 27.5}')))
    assert result["temperature_c"] == 15.0
    assert "q" not in client.get_kwargs["params"]
    assert client.get_kwargs["params"]["lat"] == 53.9
    assert client.get_kwargs["params"]["lon"] == 27.5


def test_user_location_not_executed_server_side():
    import asyncio

    result = json.loads(asyncio.run(call_tool("get_user_location", "{}")))
    assert result["error"]


def test_stream_location_request(client, mock_models, monkeypatch):
    """No known location: the loop pauses and asks the frontend for geolocation."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    plan = StreamPlan(
        stream_responses=[_sse_tool_call("get_user_location", "{}", "call_loc")],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "Какая погода?"}],
        },
    )
    assert response.status_code == 200
    assert '"type": "location_request"' in response.text
    assert '"call_loc"' in response.text
    assert plan.stream_calls == 1


def test_stream_location_resolved_from_body(client, mock_models, monkeypatch):
    """Browser already shares location: get_user_location resolves server-side."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    plan = StreamPlan(
        stream_responses=[
            _sse_tool_call("get_user_location", "{}", "call_loc"),
            _sse_text("У вас +15"),
        ],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "location": {"lat": 53.9, "lon": 27.5},
            "messages": [{"role": "user", "content": "Какая погода?"}],
        },
    )
    assert response.status_code == 200
    assert "location_request" not in response.text
    assert "У вас +15" in response.text
    assert plan.stream_calls == 2

    roles = [m["role"] for m in plan.stream_payloads[1]["messages"]]
    assert roles == ["user", "assistant", "tool"]
    tool_msg = plan.stream_payloads[1]["messages"][2]
    assert json.loads(tool_msg["content"]) == {"lat": 53.9, "lon": 27.5}
    assert "location" not in plan.stream_payloads[0]  # координаты не уходят апстрим


def test_non_stream_location_request(client, mock_models, monkeypatch):
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    plan = StreamPlan(
        stream_responses=[],
        chat_responses=[FakeChatResponse(_tool_message("get_user_location", "{}", "call_loc"))],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": False,
            "messages": [{"role": "user", "content": "Какая погода?"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "location_request"
    assert body["assistant_tool_call"]["tool_calls"][0]["id"] == "call_loc"


def test_continuation_with_coords(client, mock_models, monkeypatch):
    """Frontend re-sends with assistant tool_call + role:tool(coords); model answers."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    plan = StreamPlan(
        stream_responses=[_sse_text("В Минске +15")],
    )
    _patch(monkeypatch, plan, _fake_weather)

    assistant_msg = _tool_message("get_user_location", "{}", "call_loc")
    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Какая погода?"},
                assistant_msg,
                {
                    "role": "tool",
                    "tool_call_id": "call_loc",
                    "content": json.dumps({"lat": 53.9, "lon": 27.5}),
                },
            ],
        },
    )
    assert response.status_code == 200
    assert "В Минске +15" in response.text
    sent = plan.stream_payloads[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "tool"]


def test_continuation_location_denied(client, mock_models, monkeypatch):
    """Permission denied: tool result carries an error; model asks for the city."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    plan = StreamPlan(
        stream_responses=[_sse_text("Укажите город, например Минск")],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Какая погода?"},
                _tool_message("get_user_location", "{}", "call_loc"),
                {
                    "role": "tool",
                    "tool_call_id": "call_loc",
                    "content": json.dumps({"error": "location_unavailable"}),
                },
            ],
        },
    )
    assert response.status_code == 200
    assert "Укажите город" in response.text


async def _fake_weather(city, lat=None, lon=None):
    return json.dumps({"city": "Minsk, BY", "temperature_c": 15.0}, ensure_ascii=False)


def test_stream_plain_text_no_extra_call(client, mock_models, monkeypatch):
    """Without a tool call the stream is forwarded live; no second upstream call."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    plan = StreamPlan(
        stream_responses=[_sse_text("Привет")],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert "Привет" in response.text
    assert plan.stream_calls == 1
    assert plan.chat_calls == 0
    assert [t["function"]["name"] for t in plan.stream_payloads[0]["tools"]] == [
        "get_current_datetime",
        "download_file",
        "pravo_search",
        "pravo_get_document",
        "pzz_search_menu",
        "pzz_lookup_address",
        "pzz_place_order",
        "get_weather",
        "get_user_location",
    ]


def test_stream_tool_loop(client, mock_models, monkeypatch):
    """Tool is executed server-side and the final answer is streamed back."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    plan = StreamPlan(
        stream_responses=[
            _sse_tool_call("get_weather", '{"city": "Minsk"}', "call_1"),
            _sse_text("В Минске 15 градусов"),
        ],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "Погода в Минске?"}],
        },
    )
    assert response.status_code == 200
    assert "В Минске 15 градусов" in response.text
    assert plan.stream_calls == 2

    second = plan.stream_payloads[1]
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["user", "assistant", "tool"]
    tool_msg = second["messages"][2]
    assert tool_msg["tool_call_id"] == "call_1"
    assert json.loads(tool_msg["content"])["temperature_c"] == 15.0
    assert second["stream"] is True


def test_non_stream_tool_loop(client, mock_models, monkeypatch):
    """Non-stream /api/chat answers with final JSON after the tool run."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    plan = StreamPlan(
        stream_responses=[],
        chat_responses=[
            FakeChatResponse(_tool_message("get_weather", '{"city": "Minsk"}', "call_9")),
            FakeChatResponse({"role": "assistant", "content": "Солнечно, +15"}),
        ],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": False,
            "messages": [{"role": "user", "content": "Погода?"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Солнечно, +15"
    assert plan.chat_calls == 2
    roles = [m["role"] for m in plan.chat_payloads[1]["messages"]]
    assert roles == ["user", "assistant", "tool"]


def test_datetime_advertised_without_weather_key(client, mock_models, monkeypatch):
    """Without OPENWEATHER_API_KEY the datetime tool is still advertised, weather is not."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "")
    monkeypatch.setattr(settings, "pzz_enabled", False)
    plan = StreamPlan(stream_responses=[_sse_text("Привет")])
    _patch(monkeypatch, plan, _fake_weather)

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
    assert "get_current_datetime" in tools
    assert "get_weather" not in tools


def test_v1_proxies_tools(client, mock_models, monkeypatch, api_key):
    """/v1/chat/completions forwards client tools untouched (proxy contract)."""
    from tests.conftest import create_token

    _auth(client)
    token, _ = create_token(client, "tools-proxy")

    plan = StreamPlan(
        stream_responses=[],
        chat_responses=[FakeChatResponse({"role": "assistant", "content": "ok"})],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "x", "parameters": {}}}],
        },
    )
    assert response.status_code == 200
    assert plan.chat_payloads[0]["tools"][0]["function"]["name"] == "x"


# --- download_file tool -------------------------------------------------------


class FakeUrl:
    def __init__(self, url):
        self._url = url

    def join(self, other):
        from urllib.parse import urljoin

        return urljoin(self._url, other)

    def __str__(self):
        return self._url


class FakeDownloadResponse:
    """Response-like object for app.tools.httpx.AsyncClient().stream(...)."""

    def __init__(self, url, status_code=200, headers=None, body=b""):
        self.url = FakeUrl(url)
        self.status_code = status_code
        self.headers = headers or {}
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        yield self._body


class FakeDownloadClient:
    def __init__(self, plan):
        self.plan = list(plan)
        self.gets = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        self.gets.append((method, url))
        return self.plan.pop(0)


def _resolve_fake(mapping, default=("93.184.216.34",)):
    def fake_resolve(host):
        return list(mapping.get(host, default))

    return fake_resolve


def _make_user(db):
    from datetime import datetime, timezone

    from app.db import User

    user = User(
        email=f"dl-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _download_ctx(monkeypatch, tmp_path, resolve, plan=()):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "downloads_root", tmp_path)
    monkeypatch.setattr("app.tools._resolve_host", resolve)
    client = FakeDownloadClient(plan)
    monkeypatch.setattr("app.tools.httpx.AsyncClient", lambda *a, **kw: client)
    return client


def _run_download(arguments, user, db):
    import asyncio

    return json.loads(asyncio.run(call_tool("download_file", arguments, user=user, db=db)))


def _downloaded_files(root):
    return [p for p in root.rglob("*") if p.is_file()]


def test_download_success(monkeypatch, db, tmp_path):
    client = _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/page.txt",
                headers={"content-type": "text/plain; charset=utf-8"},
                body="Привет, мир!",
            )
        ],
    )
    user = _make_user(db)

    result = _run_download('{"url": "https://example.com/page.txt"}', user, db)
    assert result["content"] == "Привет, мир!"
    assert result["filename"] == "page.txt"
    assert result["content_type"] == "text/plain"
    assert result["size_bytes"] == len("Привет, мир!".encode("utf-8"))
    assert result["truncated"] is False
    assert client.gets == [("GET", "https://example.com/page.txt")]

    files = _downloaded_files(tmp_path)
    assert len(files) == 1
    assert files[0].read_bytes() == "Привет, мир!".encode("utf-8")
    assert ".." not in str(files[0].relative_to(tmp_path))


def test_download_missing_url(monkeypatch, db, tmp_path):
    _download_ctx(monkeypatch, tmp_path, _resolve_fake({}))
    user = _make_user(db)
    result = _run_download("{}", user, db)
    assert "url" in result["error"]


def test_download_without_user_or_db(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "downloads_root", tmp_path)
    result = _run_download('{"url": "https://example.com/x"}', None, None)
    assert "недоступно" in result["error"]


def test_download_dedup_no_second_fetch(monkeypatch, db, tmp_path):
    client = _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/page.txt",
                headers={"content-type": "text/plain"},
                body="hello",
            )
        ],
    )
    user = _make_user(db)

    first = _run_download('{"url": "https://example.com/page.txt"}', user, db)
    assert first["content"] == "hello"

    monkeypatch.setattr(
        "app.tools.httpx.AsyncClient",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("cached URL must not refetch")),
    )
    second = _run_download('{"url": "https://example.com/page.txt"}', user, db)
    assert second["cached"] is True
    assert second["content"] == "hello"
    assert second["filename"] == "page.txt"
    assert client.gets == [("GET", "https://example.com/page.txt")]


def test_download_non_text_rejected(monkeypatch, db, tmp_path):
    _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/image.png",
                headers={"content-type": "image/png"},
                body=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
            )
        ],
    )
    user = _make_user(db)

    result = _run_download('{"url": "https://example.com/image.png"}', user, db)
    assert "не поддерживается" in result["error"]
    assert _downloaded_files(tmp_path) == []


def test_download_octet_stream_sniffed_as_text(monkeypatch, db, tmp_path):
    _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/raw",
                headers={"content-type": "application/octet-stream"},
                body="just some text",
            )
        ],
    )
    user = _make_user(db)
    result = _run_download('{"url": "https://example.com/raw"}', user, db)
    assert result["content"] == "just some text"
    assert _downloaded_files(tmp_path)


def test_download_size_limit_content_length(monkeypatch, db, tmp_path):
    _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/big.bin",
                headers={"content-type": "text/plain", "content-length": "99999999"},
                body="small",
            )
        ],
    )
    user = _make_user(db)
    result = _run_download('{"url": "https://example.com/big.bin"}', user, db)
    assert "превышает лимит" in result["error"]
    assert _downloaded_files(tmp_path) == []


def test_download_size_limit_stream(monkeypatch, db, tmp_path):
    limit = get_settings().downloads_max_bytes
    _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/stream",
                headers={"content-type": "text/plain"},
                body=b"x" * (limit + 1),
            )
        ],
    )
    user = _make_user(db)
    result = _run_download('{"url": "https://example.com/stream"}', user, db)
    assert "превышает лимит" in result["error"]
    assert _downloaded_files(tmp_path) == []


def test_download_truncated_for_model(monkeypatch, db, tmp_path):
    from app.tools import _MAX_CONTENT_CHARS

    _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/long.txt",
                headers={"content-type": "text/plain"},
                body="a" * (_MAX_CONTENT_CHARS * 2),
            )
        ],
    )
    user = _make_user(db)
    result = _run_download('{"url": "https://example.com/long.txt"}', user, db)
    assert result["truncated"] is True
    assert len(result["content"]) == _MAX_CONTENT_CHARS


def test_download_filename_sanitized(monkeypatch, db, tmp_path):
    _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/..%2F..%2Fetc%2Fpasswd",
                headers={"content-type": "text/plain"},
                body="root:x:0:0",
            )
        ],
    )
    user = _make_user(db)
    result = _run_download('{"url": "https://example.com/..%2F..%2Fetc%2Fpasswd"}', user, db)
    assert result["filename"] == "passwd.txt"
    files = _downloaded_files(tmp_path)
    assert len(files) == 1
    assert files[0].name == "passwd.txt"


def test_download_follows_public_redirect(monkeypatch, db, tmp_path):
    client = _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/start",
                status_code=302,
                headers={"location": "https://example.com/real.txt"},
            ),
            FakeDownloadResponse(
                "https://example.com/real.txt",
                headers={"content-type": "text/plain"},
                body="redirected",
            ),
        ],
    )
    user = _make_user(db)
    result = _run_download('{"url": "https://example.com/start"}', user, db)
    assert result["content"] == "redirected"
    assert result["url"] == "https://example.com/real.txt"
    assert len(client.gets) == 2


@pytest.mark.parametrize(
    "ip_addr",
    ["10.0.0.5", "127.0.0.1", "192.168.1.1", "169.254.0.1", "172.16.3.3", "::1"],
)
def test_download_ssrf_private_ip_blocked(monkeypatch, db, tmp_path, ip_addr):
    client = _download_ctx(monkeypatch, tmp_path, _resolve_fake({"target.test": [ip_addr]}))
    user = _make_user(db)
    result = _run_download('{"url": "http://target.test/secret"}', user, db)
    assert "запрещён" in result["error"]
    assert client.gets == []


def test_download_ssrf_ipv4_mapped_blocked(monkeypatch, db, tmp_path):
    client = _download_ctx(
        monkeypatch, tmp_path, _resolve_fake({"target.test": ["::ffff:10.0.0.1"]})
    )
    user = _make_user(db)
    result = _run_download('{"url": "http://target.test/secret"}', user, db)
    assert "запрещён" in result["error"]
    assert client.gets == []


def test_download_redirect_to_local_blocked(monkeypatch, db, tmp_path):
    client = _download_ctx(
        monkeypatch,
        tmp_path,
        _resolve_fake({"example.com": ["93.184.216.34"], "10.0.0.1": ["10.0.0.1"]}),
        plan=[
            FakeDownloadResponse(
                "https://example.com/start",
                status_code=302,
                headers={"location": "http://10.0.0.1/secret"},
            )
        ],
    )
    user = _make_user(db)
    result = _run_download('{"url": "https://example.com/start"}', user, db)
    assert "запрещён" in result["error"]
    assert len(client.gets) == 1


def test_download_bad_scheme(monkeypatch, db, tmp_path):
    _download_ctx(monkeypatch, tmp_path, _resolve_fake({}))
    user = _make_user(db)
    result = _run_download('{"url": "file:///etc/passwd"}', user, db)
    assert "http/https" in result["error"]


async def _fake_download(arguments, user=None, db=None):
    return json.dumps(
        {"filename": "page.txt", "content": "текст страницы", "size_bytes": 10},
        ensure_ascii=False,
    )


def test_stream_download_tool_loop(client, mock_models, monkeypatch):
    """download_file runs server-side through /api/chat and final text streams back."""
    _auth(client)
    plan = StreamPlan(
        stream_responses=[
            _sse_tool_call("download_file", '{"url": "https://example.com/x"}', "call_dl"),
            _sse_text("Скачал страницу: текст страницы"),
        ],
    )
    _patch(monkeypatch, plan, _fake_weather)
    monkeypatch.setattr("app.tools._download_file", _fake_download)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "Что на этой странице?"}],
        },
    )
    assert response.status_code == 200
    assert "Скачал страницу" in response.text
    assert plan.stream_calls == 2
    roles = [m["role"] for m in plan.stream_payloads[1]["messages"]]
    assert roles == ["user", "assistant", "tool"]
    tool_msg = plan.stream_payloads[1]["messages"][2]
    assert json.loads(tool_msg["content"])["filename"] == "page.txt"


def test_call_tool_emits_otel_tool_span(monkeypatch):
    """call_tool records an OpenInference TOOL span with name/input/output."""
    from openinference.instrumentation import OITracer, TraceConfig
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import telemetry as telemetry_mod
    from app.tools import call_tool

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = OITracer(provider.get_tracer("test"), config=TraceConfig())

    monkeypatch.setattr(telemetry_mod, "tracer", tracer)
    try:
        result = asyncio.run(call_tool("get_current_datetime", '{"timezone": null}'))
    finally:
        monkeypatch.setattr(telemetry_mod, "tracer", None)
    provider.force_flush()

    assert json.loads(result)["date"]
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    attrs = span.attributes
    assert span.name == "get_current_datetime"
    assert attrs["openinference.span.kind"] == "TOOL"
    assert attrs["tool.name"] == "get_current_datetime"
    assert '"arguments": "{\\"timezone\\": null}"' in attrs["input.value"]
    assert attrs["input.mime_type"] == "application/json"
    assert json.loads(attrs["output.value"])["date"]
    assert attrs["output.mime_type"] == "application/json"


def test_tool_loop_wrapped_in_chain_span(client, mock_models, monkeypatch):
    """The /api/chat tool loop nests tool spans under a CHAIN span."""
    from openinference.instrumentation import OITracer, TraceConfig
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import telemetry as telemetry_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = OITracer(provider.get_tracer("test"), config=TraceConfig())
    monkeypatch.setattr(telemetry_mod, "tracer", tracer)

    _auth(client)
    plan = StreamPlan(
        stream_responses=[
            _sse_tool_call("get_current_datetime", "{}", "call_dt"),
            _sse_text("Готово"),
        ],
    )
    _patch(monkeypatch, plan, _fake_weather)

    response = client.post(
        "/api/chat",
        json={
            "model": "default",
            "stream": True,
            "conversation_id": "conv-sess-1",
            "messages": [{"role": "user", "content": "Который час?"}],
        },
    )
    provider.force_flush()

    assert response.status_code == 200
    assert "Готово" in response.text

    spans = exporter.get_finished_spans()
    by_kind: dict[str, list] = {}
    for s in spans:
        by_kind.setdefault(s.attributes.get("openinference.span.kind"), []).append(s)
    assert set(by_kind) == {"CHAIN", "TOOL"}
    (chain,) = by_kind["CHAIN"]
    (tool,) = by_kind["TOOL"]
    assert chain.name == "chat.tool_loop"
    assert chain.context.trace_id == tool.context.trace_id
    assert tool.parent.span_id == chain.context.span_id
    assert tool.attributes["session.id"] == "conv-sess-1"
    assert chain.attributes["session.id"] == "conv-sess-1"
    assert chain.attributes["user.id"]


def test_stream_in_session_stamps_llm_span(monkeypatch):
    """Async streamed spans (created after the handler returns) carry session.id."""
    import asyncio

    from openinference.instrumentation import OITracer, TraceConfig
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import telemetry as telemetry_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = OITracer(provider.get_tracer("test"), config=TraceConfig())
    monkeypatch.setattr(telemetry_mod, "tracer", tracer)

    sentinel = 0

    async def traced_chunks():
        nonlocal sentinel
        with tracer.start_as_current_span(
            "streamed.llm",
            openinference_span_kind="llm",
        ):
            sentinel += 1
            yield b"data: x\n\n"

    async def main():
        async for _ in telemetry_mod.stream_in_session("sess-9", traced_chunks()):
            pass

    asyncio.run(main())
    provider.force_flush()

    assert sentinel == 1
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["session.id"] == "sess-9"


def test_llm_byte_stream_survives_parent_span_exit(monkeypatch, caplog):
    """Tool-loop peek starts the LLM stream under chain_span; finishing it after
    that span exits must not log Failed to detach context."""
    import asyncio
    import logging

    from openinference.instrumentation import OITracer, TraceConfig
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import telemetry as telemetry_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = OITracer(provider.get_tracer("test"), config=TraceConfig())
    monkeypatch.setattr(telemetry_mod, "tracer", tracer)

    async def raw():
        yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    async def main():
        gen = telemetry_mod.llm_byte_stream(
            "test.llm",
            raw(),
            input_payload={
                "model": "openrouter/free",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        with tracer.start_as_current_span("parent", openinference_span_kind="chain"):
            assert await gen.__anext__() == b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        rest = [chunk async for chunk in gen]
        assert rest == [b"data: [DONE]\n\n"]

    with caplog.at_level(logging.ERROR):
        asyncio.run(main())
    provider.force_flush()
    assert "Failed to detach context" not in caplog.text
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "test.llm" in spans
    assert "parent" in spans
    llm = spans["test.llm"]
    from opentelemetry.trace import StatusCode

    assert llm.status.status_code == StatusCode.OK
    assert llm.attributes["output.value"] == "Hi"
    assert "hi" in str(llm.attributes.get("input.value", ""))


def test_compact_tool_content_summarizes_pzz_menu():
    from app.telemetry import compact_tool_content

    raw = json.dumps(
        {
            "source": "pzz.by",
            "query": "пепперони",
            "count": 2,
            "items": [
                {
                    "id": 1,
                    "title": "Пепперони",
                    "photo": "https://example.com/huge.jpg",
                    "offers": [{"size": "big", "price_byn": 40.5}],
                },
                {"id": 2, "title": "Пепперони острая", "photo": "https://example.com/2.jpg"},
            ],
        },
        ensure_ascii=False,
    )
    compact = json.loads(compact_tool_content(raw))
    assert compact["source"] == "pzz.by"
    assert compact["titles"] == ["Пепперони", "Пепперони острая"]
    assert compact["count"] == 2
    assert "photo" not in json.dumps(compact)

    pravo = json.loads(
        compact_tool_content(
            json.dumps(
                {
                    "source": "pravo.by",
                    "query": "трудовой",
                    "count": 1,
                    "documents": [{"title": "Трудовой кодекс", "url": "https://pravo.by/x"}],
                },
                ensure_ascii=False,
            )
        )
    )
    assert pravo["titles"] == ["Трудовой кодекс"]
    assert "url" not in json.dumps(pravo)


def test_compact_tool_content_other_shapes():
    from app.telemetry import compact_tool_content, payload_for_trace

    assert compact_tool_content("not-json") == "not-json"
    assert json.loads(compact_tool_content("[1, 2]")) == [1, 2]
    compact = json.loads(
        compact_tool_content(
            json.dumps(
                {
                    "source": "pzz.by",
                    "items": [{"title": "X"}],
                    "error": "boom",
                    "order_num": 9,
                    "submitted": True,
                }
            )
        )
    )
    assert compact["error"] == "boom"
    assert compact["order_num"] == 9
    assert compact["submitted"] is True
    other = json.loads(compact_tool_content(json.dumps({"hello": "world"})))
    assert other == {"hello": "world"}
    traced = payload_for_trace(
        {
            "model": "default",
            "messages": [
                "skip-me",
                {"role": "user", "content": "hi"},
                {"role": "tool", "content": json.dumps({"source": "pzz.by", "items": []})},
            ],
        }
    )
    assert traced["model"] == "default"
    assert traced["messages"][0]["role"] == "user"
    tool_payload = json.loads(traced["messages"][1]["content"])
    assert tool_payload["source"] == "pzz.by"
    assert "titles" in tool_payload


def test_llm_byte_stream_records_tool_calls(monkeypatch):
    import asyncio

    from openinference.instrumentation import OITracer, TraceConfig
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import telemetry as telemetry_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = OITracer(provider.get_tracer("test"), config=TraceConfig())
    monkeypatch.setattr(telemetry_mod, "tracer", tracer)

    chunks = _sse_tool_call("pzz_search_menu", '{"query": "пицца"}', "call_menu")

    async def raw():
        for chunk in chunks:
            yield chunk

    asyncio.run(
        _aiter_all(
            telemetry_mod.llm_byte_stream(
                "test.llm",
                raw(),
                input_payload={"model": "x", "messages": [{"role": "user", "content": "меню"}]},
            )
        )
    )
    provider.force_flush()
    llm = next(span for span in exporter.get_finished_spans() if span.name == "test.llm")
    assert "pzz_search_menu" in llm.attributes["output.value"]
    assert any(
        value == "pzz_search_menu"
        for key, value in llm.attributes.items()
        if "tool_call" in key and "name" in key
    )


async def _aiter_all(gen):
    async for _ in gen:
        pass
