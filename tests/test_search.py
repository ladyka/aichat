import asyncio
import base64
import json
import uuid

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import WebSearch, get_user_by_email
from app.search import parse_brave_payload, parse_yandex_xml, search_brave, search_yandex
from app.tools import call_tool, enabled_tools, tool_progress_line
from tests.conftest import register

_YANDEX_XML = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch version="1.0">
  <response>
    <results>
      <grouping>
        <group>
          <doc>
            <url>https://example.com/minsk</url>
            <title>Погода в <hlword>Минске</hlword></title>
            <passages>
              <passage>Сегодня <hlword>ясно</hlword>.</passage>
            </passages>
          </doc>
        </group>
      </grouping>
    </results>
  </response>
</yandexsearch>
"""

_YANDEX_ERROR = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch><response><error code="15">Ничего не найдено</error></response></yandexsearch>
"""


class _Response:
    def __init__(self, status, *, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Client:
    last_init: dict = {}
    last_get: dict = {}
    last_post: dict = {}
    response = _Response(200, payload={})

    def __init__(self, *args, **kwargs):
        _Client.last_init = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        _Client.last_get = {"url": url, "params": params, "headers": headers}
        return _Client.response

    async def post(self, url, json=None, headers=None):
        _Client.last_post = {"url": url, "json": json, "headers": headers}
        return _Client.response


def _enable_brave(monkeypatch, key="brave-secret"):
    monkeypatch.setattr(get_settings(), "brave_search_api_key", key)


def _enable_yandex(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "yandex_search_api_key", "ya-key")
    monkeypatch.setattr(settings, "yandex_search_folder_id", "b1gfolder")


def test_web_search_progress_line():
    assert tool_progress_line("web_search") == "Ищу в интернете…"


def test_web_search_hidden_without_keys():
    settings = get_settings()
    assert settings.brave_search_api_key == ""
    assert settings.yandex_search_api_key == ""
    assert "web_search" not in [t["function"]["name"] for t in enabled_tools()]


def test_web_search_advertised_for_each_engine(monkeypatch):
    _enable_brave(monkeypatch)
    brave_tool = next(t for t in enabled_tools() if t["function"]["name"] == "web_search")
    assert "Brave" in brave_tool["function"]["description"]
    assert "engine" not in brave_tool["function"]["parameters"]["properties"]

    monkeypatch.setattr(get_settings(), "brave_search_api_key", "")
    _enable_yandex(monkeypatch)
    yandex_tool = next(t for t in enabled_tools() if t["function"]["name"] == "web_search")
    assert "Яндекс" in yandex_tool["function"]["description"]

    _enable_brave(monkeypatch)
    both = next(t for t in enabled_tools() if t["function"]["name"] == "web_search")
    assert both["function"]["parameters"]["properties"]["engine"]["enum"] == ["brave", "yandex"]


def test_parse_brave_and_yandex_documents():
    brave = parse_brave_payload(
        {
            "web": {
                "results": [
                    {"title": "Минск", "url": "https://example.com/a", "description": "Столица"},
                    {"title": "мимо", "url": "ftp://example.com/no", "description": "нет"},
                ]
            }
        },
        limit=5,
    )
    assert brave["results"] == [
        {
            "source": "Brave",
            "title": "Минск",
            "url": "https://example.com/a",
            "snippet": "Столица",
        }
    ]
    yandex = parse_yandex_xml(_YANDEX_XML, limit=5)
    assert yandex["results"][0]["title"] == "Погода в Минске"
    assert yandex["results"][0]["snippet"] == "Сегодня ясно."
    assert yandex["results"][0]["url"] == "https://example.com/minsk"
    assert parse_yandex_xml(_YANDEX_ERROR, limit=5)["error"] == "Ничего не найдено"


def test_search_brave_sends_subscription_token(monkeypatch):
    _enable_brave(monkeypatch)
    _Client.response = _Response(
        200,
        payload={
            "web": {"results": [{"title": "A", "url": "https://a.example", "description": "s"}]}
        },
    )
    monkeypatch.setattr("app.search.httpx.AsyncClient", _Client)
    result = asyncio.run(search_brave("погода", count=3))
    assert result["results"][0]["url"] == "https://a.example"
    assert _Client.last_init["follow_redirects"] is False
    assert _Client.last_get["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert _Client.last_get["headers"]["X-Subscription-Token"] == "brave-secret"
    assert _Client.last_get["params"]["q"] == "погода"
    assert _Client.last_get["params"]["search_lang"] == "ru"
    assert _Client.last_get["params"]["country"] == "ALL"


def test_search_yandex_posts_search_api_v2(monkeypatch):
    _enable_yandex(monkeypatch)
    encoded = base64.b64encode(_YANDEX_XML.encode()).decode()
    _Client.response = _Response(200, payload={"rawData": encoded})
    monkeypatch.setattr("app.search.httpx.AsyncClient", _Client)
    result = asyncio.run(search_yandex("минск", count=2))
    assert result["results"][0]["source"] == "Яндекс"
    assert result["results"][0]["url"] == "https://example.com/minsk"
    assert _Client.last_init["follow_redirects"] is False
    assert _Client.last_post["url"] == "https://searchapi.api.cloud.yandex.net/v2/web/search"
    assert _Client.last_post["headers"]["Authorization"] == "Api-Key ya-key"
    body = _Client.last_post["json"]
    assert body["folderId"] == "b1gfolder"
    assert body["query"]["searchType"] == "SEARCH_TYPE_RU"
    assert body["query"]["queryText"] == "минск"
    assert body["groupSpec"]["groupsOnPage"] == "2"
    assert body["responseFormat"] == "FORMAT_XML"


def test_search_yandex_http_error(monkeypatch):
    _enable_yandex(monkeypatch)
    _Client.response = _Response(401, payload={})
    monkeypatch.setattr("app.search.httpx.AsyncClient", _Client)
    result = asyncio.run(search_yandex("минск", count=1))
    assert result["error"] == "Яндекс ответил ошибкой 401."


def test_web_search_merges_engines_and_counts_the_day(monkeypatch, client, db):
    _enable_brave(monkeypatch)
    _enable_yandex(monkeypatch)
    monkeypatch.setattr(get_settings(), "web_search_daily_limit", 2)
    monkeypatch.setattr(get_settings(), "web_search_result_count", 5)
    email = f"search-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)

    async def fake_brave(query, *, count):
        return {
            "source": "Brave",
            "results": [
                {
                    "source": "Brave",
                    "title": "Курс",
                    "url": "https://brave.example/usd",
                    "snippet": "3.2",
                }
            ],
        }

    async def fake_yandex(query, *, count):
        return {"source": "Яндекс", "error": "Яндекс ответил ошибкой 503."}

    monkeypatch.setattr("app.search.search_brave", fake_brave)
    monkeypatch.setattr("app.search.search_yandex", fake_yandex)
    first = json.loads(
        asyncio.run(call_tool("web_search", '{"query": " курс   доллара "}', user, db))
    )
    assert first["query"] == "курс доллара"
    assert [item["source"] for item in first["results"]] == ["Brave"]
    assert first["errors"][0]["source"] == "Яндекс"
    assert _search_count(db, user.id) == 1

    second = json.loads(
        asyncio.run(call_tool("web_search", '{"query": "ещё", "engine": "brave"}', user, db))
    )
    assert second["results"][0]["url"] == "https://brave.example/usd"
    blocked = json.loads(asyncio.run(call_tool("web_search", '{"query": "третий"}', user, db)))
    assert "лимит" in blocked["error"]
    assert _search_count(db, user.id) == 2


def _search_count(db, user_id: int) -> int:
    return int(
        db.scalar(select(func.count()).select_from(WebSearch).where(WebSearch.user_id == user_id))
        or 0
    )


def test_web_search_rejects_unknown_engine(monkeypatch, client, db):
    _enable_brave(monkeypatch)
    email = f"search-engine-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)
    result = json.loads(
        asyncio.run(call_tool("web_search", '{"query": "минск", "engine": "google"}', user, db))
    )
    assert "brave или yandex" in result["error"]


def test_search_settings_from_env(monkeypatch):
    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "key")
    monkeypatch.setenv("YANDEX_SEARCH_FOLDER_ID", "folder")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "")
    settings = Settings()
    assert settings.yandex_search_enabled
    assert settings.web_search_enabled
    assert not settings.brave_search_enabled
