import asyncio
import json

import pytest
from fastapi import HTTPException

from app.model_providers import ol


class _Message:
    def __init__(self, content="", tool_calls=None):
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls


class FakeChatResponse:
    def __init__(self, content="Привет!", prompt_eval_count=10, eval_count=20):
        self.message = _Message(content=content)
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count


def _enable(monkeypatch, enabled=True):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ol_enabled", enabled)


def test_list_models_disabled(monkeypatch):
    _enable(monkeypatch, False)
    assert asyncio.run(ol.list_models()) == []


def test_chat_requires_config(monkeypatch):
    _enable(monkeypatch, False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ol.chat_completions({"model": "deepseek-v4.1-flash:cloud"}))
    assert exc.value.status_code == 503


def test_to_ollama_messages_translates_tools():
    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Какая погода?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Минск"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_abc", "content": "+18°C"},
    ]
    translated = ol._to_ollama_messages(messages)
    assert translated[0] == {"role": "system", "content": "You are helpful"}
    assert translated[1] == {"role": "user", "content": "Какая погода?"}
    assert translated[2]["tool_calls"] == [
        {"function": {"name": "get_weather", "arguments": {"city": "Минск"}}}
    ]
    assert translated[3] == {"role": "tool", "content": "+18°C", "tool_name": "get_weather"}


def test_to_ollama_request_maps_options():
    request = ol._to_ollama_request(
        {
            "model": "deepseek-v4.1-flash:cloud",
            "messages": [{"role": "user", "content": "Hello!"}],
            "temperature": 0.5,
            "max_tokens": 100,
            "stop": ["END"],
            "tools": [{"type": "function", "function": {"name": "t"}}],
        }
    )
    assert request["model"] == "deepseek-v4.1-flash:cloud"
    assert request["options"] == {"temperature": 0.5, "num_predict": 100, "stop": ["END"]}
    assert request["tools"] == [{"type": "function", "function": {"name": "t"}}]
    assert "stream" not in request
    assert "think" not in request


def test_to_ollama_request_passes_think():
    request = ol._to_ollama_request(
        {
            "model": "glm-5.3-flash:cloud",
            "messages": [{"role": "user", "content": "Hi"}],
            "think": False,
        }
    )
    assert request["think"] is False


def test_to_openai_response_wraps_content():
    data = ol._to_openai_response(
        "deepseek-v4.1-flash:cloud",
        FakeChatResponse(content="Привет!", prompt_eval_count=10, eval_count=20),
    )
    assert data["object"] == "chat.completion"
    assert data["model"] == "deepseek-v4.1-flash:cloud"
    choice = data["choices"][0]
    assert choice["message"] == {"role": "assistant", "content": "Привет!"}
    assert choice["finish_reason"] == "stop"
    assert data["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
    }


def test_chat_completions_returns_openai_response(monkeypatch):
    _enable(monkeypatch)

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def chat(self, **kw):
            assert kw["model"] == "deepseek-v4.1-flash:cloud"
            return FakeChatResponse(content="Привет!")

    monkeypatch.setattr(ol, "_client", FakeClient)
    response = asyncio.run(ol.chat_completions({"model": "deepseek-v4.1-flash:cloud"}))
    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "Привет!"


def test_stream_chat_completions_emits_sse(monkeypatch):
    _enable(monkeypatch)

    class FakeChunk:
        def __init__(self, content="", done=False):
            self.message = _Message(content=content)
            self.done = done
            self.prompt_eval_count = 3
            self.eval_count = 5

    async def fake_stream(**kw):
        yield FakeChunk(content="Прив")
        yield FakeChunk(content="ет!")
        yield FakeChunk(done=True)

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def chat(self, **kw):
            assert kw["stream"] is True
            return fake_stream(**kw)

    monkeypatch.setattr(ol, "_client", FakeClient)

    async def collect():
        chunks = []
        async for chunk in ol.stream_chat_completions(
            {"model": "deepseek-v4.1-flash:cloud", "stream": True}
        ):
            chunks.append(chunk)
        return chunks

    raw = b"".join(asyncio.run(collect()))
    lines = [
        json.loads(line[5:].decode("utf-8"))
        for line in raw.split(b"\n")
        if line.strip().startswith(b"data:") and b"[DONE]" not in line
    ]
    assert lines[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    contents = "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in lines[1:])
    assert "Прив" in contents and "ет!" in contents
    assert lines[-1]["choices"][0]["finish_reason"] == "stop"
    assert lines[-1]["usage"]["total_tokens"] == 8
    assert raw.endswith(b"data: [DONE]\n\n")
