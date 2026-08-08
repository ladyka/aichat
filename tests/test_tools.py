import json
import uuid

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


def test_enabled_tools_without_key():
    settings = get_settings()
    settings.openweather_api_key = ""
    assert enabled_tools() == []


def test_enabled_tools_with_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "ow-test")
    tools = enabled_tools()
    assert [t["function"]["name"] for t in tools] == ["get_weather", "get_user_location"]


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
    assert plan.stream_payloads[0]["tools"][0]["function"]["name"] == "get_weather"


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


def test_no_tools_without_key(client, mock_models, monkeypatch):
    """Without OPENWEATHER_API_KEY no tools are advertised to the model."""
    _auth(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "")
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
    assert "tools" not in plan.stream_payloads[0]


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
