"""Веб-поиск для чата: Brave Search API и Yandex Search API v2."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx2 as httpx

from app.config import get_settings

logger = logging.getLogger("aichat.search")

_MAX_QUERY_CHARS = 400
_MAX_SNIPPET_CHARS = 400
_TIMEOUT = 20.0

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_YANDEX_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"


def normalize_query(raw: Any) -> str:
    """Сжать пробелы и обрезать запрос до лимита Yandex Search API."""
    text = " ".join(str(raw or "").split())
    return text[:_MAX_QUERY_CHARS]


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _MAX_SNIPPET_CHARS:
        return collapsed
    return collapsed[: _MAX_SNIPPET_CHARS - 1].rstrip() + "…"


def _http_url(value: str) -> str:
    url = value.strip()
    if url.startswith("https://") or url.startswith("http://"):
        return url
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_text(node: ET.Element) -> str:
    return " ".join("".join(node.itertext()).split())


def parse_brave_payload(payload: Any, *, limit: int) -> dict[str, Any]:
    """Разобрать JSON Brave Web Search: web.results[]."""
    if not isinstance(payload, dict):
        return {"source": "Brave", "error": "Brave вернул неразборчивый ответ."}
    message = str(payload.get("message") or "").strip()
    if message and "web" not in payload:
        return {"source": "Brave", "error": message}
    web = payload.get("web")
    values = web.get("results") if isinstance(web, dict) else None
    if not isinstance(values, list):
        values = []
    results: list[dict[str, str]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        url = _http_url(str(item.get("url") or ""))
        if not url:
            continue
        results.append(
            {
                "source": "Brave",
                "title": _clip(str(item.get("title") or url)),
                "url": url,
                "snippet": _clip(str(item.get("description") or "")),
            }
        )
        if len(results) >= limit:
            break
    return {"source": "Brave", "results": results}


def parse_yandex_xml(text: str, *, limit: int) -> dict[str, Any]:
    """Разобрать XML из ответа Yandex Search API v2: doc/url, title, passages."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {"source": "Яндекс", "error": "Яндекс вернул неразборчивый ответ."}

    error = ""
    results: list[dict[str, str]] = []
    for node in root.iter():
        name = _local(node.tag)
        if name == "error" and not error:
            error = _clip(_xml_text(node))
            continue
        if name != "doc":
            continue
        url = ""
        title = ""
        passages: list[str] = []
        for child in list(node):
            child_name = _local(child.tag)
            if child_name == "url":
                url = _http_url(_xml_text(child))
            elif child_name == "title":
                title = _clip(_xml_text(child))
            elif child_name == "headline" and not passages:
                headline = _clip(_xml_text(child))
                if headline:
                    passages.append(headline)
            elif child_name == "passages":
                for passage in list(child):
                    if _local(passage.tag) != "passage":
                        continue
                    snippet = _clip(_xml_text(passage))
                    if snippet:
                        passages.append(snippet)
        if not url:
            continue
        results.append(
            {
                "source": "Яндекс",
                "title": title or url,
                "url": url,
                "snippet": " ".join(passages[:2]),
            }
        )
        if len(results) >= limit:
            break
    if error and not results:
        return {"source": "Яндекс", "error": error}
    return {"source": "Яндекс", "results": results}


async def search_brave(query: str, *, count: int) -> dict[str, Any]:
    """GET Brave Web Search. Ключ — заголовок X-Subscription-Token."""
    settings = get_settings()
    if not settings.brave_search_api_key:
        return {"source": "Brave", "error": "Поиск Brave не настроен."}
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": settings.brave_search_api_key,
    }
    params = {
        "q": query,
        "count": count,
        "country": "ALL",
        "search_lang": "ru",
        "ui_lang": "ru-RU",
        "safesearch": "moderate",
        "text_decorations": "false",
        "result_filter": "web",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = await client.get(_BRAVE_URL, params=params, headers=headers)
    except httpx.HTTPError:
        logger.warning("brave search failed error=transport")
        return {"source": "Brave", "error": "Не удалось связаться с Brave."}
    if response.status_code >= 400:
        logger.warning("brave search failed status=%s", response.status_code)
        return {"source": "Brave", "error": f"Brave ответил ошибкой {response.status_code}."}
    try:
        payload = response.json()
    except ValueError:
        return {"source": "Brave", "error": "Brave вернул неразборчивый ответ."}
    return parse_brave_payload(payload, limit=count)


def _yandex_body(query: str, count: int, folder_id: str) -> dict[str, Any]:
    return {
        "query": {
            "searchType": "SEARCH_TYPE_RU",
            "queryText": query,
            "familyMode": "FAMILY_MODE_MODERATE",
            "page": "0",
        },
        "groupSpec": {
            "groupMode": "GROUP_MODE_FLAT",
            "groupsOnPage": str(count),
            "docsInGroup": "1",
        },
        "l10n": "LOCALIZATION_RU",
        "folderId": folder_id,
        "responseFormat": "FORMAT_XML",
    }


def _yandex_xml_from_payload(payload: Any) -> tuple[str, str]:
    """Достать XML из поля rawData. Вторая строка — текст ошибки, если XML нет."""
    if not isinstance(payload, dict):
        return "", "Яндекс вернул неразборчивый ответ."
    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        if message:
            return "", message
    raw = payload.get("rawData")
    if not isinstance(raw, str) or not raw.strip():
        message = str(payload.get("message") or "").strip()
        return "", message or "Яндекс вернул неразборчивый ответ."
    try:
        xml = base64.b64decode("".join(raw.split()), validate=True)
    except (binascii.Error, ValueError):
        return "", "Яндекс вернул неразборчивый ответ."
    return xml.decode("utf-8", errors="replace"), ""


async def search_yandex(query: str, *, count: int) -> dict[str, Any]:
    """POST Yandex Search API v2. Ключ — Authorization: Api-Key, каталог — folderId."""
    settings = get_settings()
    if not (settings.yandex_search_api_key and settings.yandex_search_folder_id):
        return {"source": "Яндекс", "error": "Поиск Яндекса не настроен."}
    headers = {
        "Authorization": f"Api-Key {settings.yandex_search_api_key}",
        "Content-Type": "application/json",
    }
    body = _yandex_body(query, count, settings.yandex_search_folder_id)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = await client.post(_YANDEX_URL, json=body, headers=headers)
    except httpx.HTTPError:
        logger.warning("yandex search failed error=transport")
        return {"source": "Яндекс", "error": "Не удалось связаться с Яндексом."}
    if response.status_code >= 400:
        logger.warning("yandex search failed status=%s", response.status_code)
        return {"source": "Яндекс", "error": f"Яндекс ответил ошибкой {response.status_code}."}
    try:
        payload = response.json()
    except ValueError:
        return {"source": "Яндекс", "error": "Яндекс вернул неразборчивый ответ."}
    xml, error = _yandex_xml_from_payload(payload)
    if error:
        return {"source": "Яндекс", "error": error}
    return parse_yandex_xml(xml, limit=count)


async def run_web_search(query: str, engines: list[str], *, count: int) -> dict[str, Any]:
    """Обойти выбранные службы и склеить ссылки. Сбой одной не прячет ответ другой."""
    tasks = []
    if "brave" in engines:
        tasks.append(search_brave(query, count=count))
    if "yandex" in engines:
        tasks.append(search_yandex(query, count=count))
    parts = await asyncio.gather(*tasks)
    results: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for part in parts:
        hits = part.get("results")
        if isinstance(hits, list):
            results.extend(hit for hit in hits if isinstance(hit, dict))
        error = str(part.get("error") or "").strip()
        if error:
            errors.append({"source": str(part.get("source") or ""), "error": error})
    payload: dict[str, Any] = {"query": query, "results": results}
    if errors:
        payload["errors"] = errors
    return payload
