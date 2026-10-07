import asyncio
import json
import uuid

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import WebSearch, get_user_by_email
from app.search import parse_bing_payload, parse_yandex_xml, search_bing, search_yandex
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


def _enable_bing(monkeypatch, key="bing-secret"):
    settings = get_settings()
    monkeypatch.setattr(settings, "bing_search_api_key", key)
    monkeypatch.setattr(
        settings, "bing_search_endpoint", "https://api.bing.microsoft.com/v7.0/search"
    )
    monkeypatch.setattr(settings, "bing_search_mkt", "ru-RU")


def _enable_yandex_cloud(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "yandex_search_api_key", "ya-key")
    monkeypatch.setattr(settings, "yandex_search_folder_id", "b1gfolder")
    monkeypatch.setattr(settings, "yandex_xml_user", "")
    monkeypatch.setattr(settings, "yandex_xml_key", "")
    monkeypatch.setattr(settings, "yandex_search_xml_url", "https://yandex.ru/search/xml")
    monkeypatch.setattr(settings, "yandex_search_lr", "149")


def test_web_search_progress_line():
    assert tool_progress_line("web_search") == "Ищу в интернете…"


def test_web_search_hidden_without_keys():
    settings = get_settings()
    assert settings.bing_search_api_key == ""
    assert "web_search" not in [t["function"]["name"] for t in enabled_tools()]


def test_web_search_advertised_for_each_engine(monkeypatch):
    _enable_bing(monkeypatch)
    bing_tool = next(t for t in enabled_tools() if t["function"]["name"] == "web_search")
    assert "Bing" in bing_tool["function"]["description"]
    assert "engine" not in bing_tool["function"]["parameters"]["properties"]

    monkeypatch.setattr(get_settings(), "bing_search_api_key", "")
    _enable_yandex_cloud(monkeypatch)
    yandex_tool = next(t for t in enabled_tools() if t["function"]["name"] == "web_search")
    assert "Яндекс" in yandex_tool["function"]["description"]

    _enable_bing(monkeypatch)
    both = next(t for t in enabled_tools() if t["function"]["name"] == "web_search")
    assert both["function"]["parameters"]["properties"]["engine"]["enum"] == ["bing", "yandex"]


def test_parse_bing_and_yandex_documents():
    bing = parse_bing_payload(
        {
            "webPages": {
                "value": [
                    {"name": "Минск", "url": "https://example.com/a", "snippet": "Столица"},
                    {"name": "мимо", "url": "ftp://example.com/no", "snippet": "нет"},
                ]
            }
        },
        limit=5,
    )
    assert bing["results"] == [
        {
            "source": "Bing",
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


def test_search_bing_sends_subscription_key(monkeypatch):
    _enable_bing(monkeypatch)
    _Client.response = _Response(
        200,
        payload={
            "webPages": {"value": [{"name": "A", "url": "https://a.example", "snippet": "s"}]}
        },
    )
    monkeypatch.setattr("app.search.httpx.AsyncClient", _Client)
    result = asyncio.run(search_bing("погода", count=3))
    assert result["results"][0]["url"] == "https://a.example"
    assert _Client.last_init["follow_redirects"] is False
    assert _Client.last_get["headers"]["Ocp-Apim-Subscription-Key"] == "bing-secret"
    assert _Client.last_get["params"]["q"] == "погода"
    assert _Client.last_get["params"]["mkt"] == "ru-RU"


def test_search_yandex_prefers_cloud_key(monkeypatch):
    _enable_yandex_cloud(monkeypatch)
    monkeypatch.setattr(get_settings(), "yandex_xml_user", "legacy-user")
    monkeypatch.setattr(get_settings(), "yandex_xml_key", "legacy-key")
    _Client.response = _Response(200, text=_YANDEX_XML)
    monkeypatch.setattr("app.search.httpx.AsyncClient", _Client)
    result = asyncio.run(search_yandex("минск", count=2))
    assert result["results"][0]["source"] == "Яндекс"
    params = _Client.last_get["params"]
    assert params["folderid"] == "b1gfolder"
    assert params["apikey"] == "ya-key"
    assert "user" not in params
    assert params["lr"] == "149"
    assert "groups-on-page=2" in params["groupby"]


def test_search_yandex_classic_user_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "yandex_search_api_key", "")
    monkeypatch.setattr(settings, "yandex_search_folder_id", "")
    monkeypatch.setattr(settings, "yandex_xml_user", "xml-user")
    monkeypatch.setattr(settings, "yandex_xml_key", "xml-key")
    monkeypatch.setattr(settings, "yandex_search_xml_url", "https://yandex.ru/search/xml")
    monkeypatch.setattr(settings, "yandex_search_lr", "")
    _Client.response = _Response(401, text="")
    monkeypatch.setattr("app.search.httpx.AsyncClient", _Client)
    result = asyncio.run(search_yandex("минск", count=1))
    assert result["error"] == "Яндекс ответил ошибкой 401."
    assert _Client.last_get["params"]["user"] == "xml-user"
    assert _Client.last_get["params"]["key"] == "xml-key"


def test_web_search_merges_engines_and_counts_the_day(monkeypatch, client, db):
    _enable_bing(monkeypatch)
    _enable_yandex_cloud(monkeypatch)
    monkeypatch.setattr(get_settings(), "web_search_daily_limit", 2)
    monkeypatch.setattr(get_settings(), "web_search_result_count", 5)
    email = f"search-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)

    async def fake_bing(query, *, count):
        return {
            "source": "Bing",
            "results": [
                {
                    "source": "Bing",
                    "title": "Курс",
                    "url": "https://bing.example/usd",
                    "snippet": "3.2",
                }
            ],
        }

    async def fake_yandex(query, *, count):
        return {"source": "Яндекс", "error": "Яндекс ответил ошибкой 503."}

    monkeypatch.setattr("app.search.search_bing", fake_bing)
    monkeypatch.setattr("app.search.search_yandex", fake_yandex)
    # call_tool imports run_web_search, which calls the functions above.
    first = json.loads(
        asyncio.run(call_tool("web_search", '{"query": " курс   доллара "}', user, db))
    )
    assert first["query"] == "курс доллара"
    assert [item["source"] for item in first["results"]] == ["Bing"]
    assert first["errors"][0]["source"] == "Яндекс"
    assert _search_count(db, user.id) == 1

    second = json.loads(
        asyncio.run(call_tool("web_search", '{"query": "ещё", "engine": "bing"}', user, db))
    )
    assert second["results"][0]["url"] == "https://bing.example/usd"
    blocked = json.loads(asyncio.run(call_tool("web_search", '{"query": "третий"}', user, db)))
    assert "лимит" in blocked["error"]
    assert _search_count(db, user.id) == 2


def _search_count(db, user_id: int) -> int:
    return int(
        db.scalar(select(func.count()).select_from(WebSearch).where(WebSearch.user_id == user_id))
        or 0
    )


def test_web_search_rejects_unknown_engine(monkeypatch, client, db):
    _enable_bing(monkeypatch)
    email = f"search-engine-{uuid.uuid4().hex[:8]}@example.com"
    register(client, email)
    user = get_user_by_email(db, email)
    result = json.loads(
        asyncio.run(call_tool("web_search", '{"query": "минск", "engine": "google"}', user, db))
    )
    assert "bing или yandex" in result["error"]


def test_yandex_cloud_settings_from_env(monkeypatch):
    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "key")
    monkeypatch.setenv("YANDEX_SEARCH_FOLDER_ID", "folder")
    monkeypatch.setenv("BING_SEARCH_API_KEY", "")
    settings = Settings()
    assert settings.yandex_search_enabled
    assert settings.web_search_enabled
    assert not settings.bing_search_enabled
