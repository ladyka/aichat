"""Веб-поиск для чата: Bing Web Search и Яндекс XML (Yandex Search API)."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx2 as httpx

from app.config import get_settings

logger = logging.getLogger("aichat.search")

_MAX_QUERY_CHARS = 400
_MAX_SNIPPET_CHARS = 400
_TIMEOUT = 20.0


def normalize_query(raw: Any) -> str:
    """Сжать пробелы и обрезать запрос до лимита Яндекс XML."""
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


def parse_bing_payload(payload: Any, *, limit: int) -> dict[str, Any]:
    """Разобрать JSON Bing Web Search v7: webPages.value[]."""
    if not isinstance(payload, dict):
        return {"source": "Bing", "error": "Bing вернул неразборчивый ответ."}
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        message = ""
        first = errors[0]
        if isinstance(first, dict):
            message = str(first.get("message") or "").strip()
        return {"source": "Bing", "error": message or "Bing отклонил запрос."}
    pages = payload.get("webPages")
    values = pages.get("value") if isinstance(pages, dict) else None
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
                "source": "Bing",
                "title": _clip(str(item.get("name") or url)),
                "url": url,
                "snippet": _clip(str(item.get("snippet") or "")),
            }
        )
        if len(results) >= limit:
            break
    return {"source": "Bing", "results": results}


def parse_yandex_xml(text: str, *, limit: int) -> dict[str, Any]:
    """Разобрать ответ Яндекс XML: doc/url, title, passages."""
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


async def search_bing(query: str, *, count: int) -> dict[str, Any]:
    """GET Bing Web Search v7. Ключ — заголовок Ocp-Apim-Subscription-Key."""
    settings = get_settings()
    if not settings.bing_search_api_key:
        return {"source": "Bing", "error": "Поиск Bing не настроен."}
    headers = {"Ocp-Apim-Subscription-Key": settings.bing_search_api_key}
    params = {
        "q": query,
        "count": count,
        "mkt": settings.bing_search_mkt,
        "responseFilter": "Webpages",
        "textDecorations": "false",
        "textFormat": "Raw",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = await client.get(
                settings.bing_search_endpoint, params=params, headers=headers
            )
    except httpx.HTTPError:
        logger.warning("bing search failed error=transport")
        return {"source": "Bing", "error": "Не удалось связаться с Bing."}
    if response.status_code >= 400:
        logger.warning("bing search failed status=%s", response.status_code)
        return {"source": "Bing", "error": f"Bing ответил ошибкой {response.status_code}."}
    try:
        payload = response.json()
    except ValueError:
        return {"source": "Bing", "error": "Bing вернул неразборчивый ответ."}
    return parse_bing_payload(payload, limit=count)


async def search_yandex(query: str, *, count: int) -> dict[str, Any]:
    """GET Яндекс XML. Облачный ключ (folderid + apikey) важнее классической пары user + key."""
    settings = get_settings()
    params: dict[str, Any] = {
        "query": query,
        "l10n": "ru",
        "filter": "moderate",
        "groupby": f"attr=d.mode=deep.groups-on-page={count}.docs-in-group=1",
    }
    if settings.yandex_search_api_key and settings.yandex_search_folder_id:
        params["folderid"] = settings.yandex_search_folder_id
        params["apikey"] = settings.yandex_search_api_key
    elif settings.yandex_xml_user and settings.yandex_xml_key:
        params["user"] = settings.yandex_xml_user
        params["key"] = settings.yandex_xml_key
    else:
        return {"source": "Яндекс", "error": "Поиск Яндекс XML не настроен."}
    if settings.yandex_search_lr:
        params["lr"] = settings.yandex_search_lr
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = await client.get(settings.yandex_search_xml_url, params=params)
    except httpx.HTTPError:
        logger.warning("yandex xml search failed error=transport")
        return {"source": "Яндекс", "error": "Не удалось связаться с Яндексом."}
    if response.status_code >= 400:
        logger.warning("yandex xml search failed status=%s", response.status_code)
        return {"source": "Яндекс", "error": f"Яндекс ответил ошибкой {response.status_code}."}
    return parse_yandex_xml(response.text, limit=count)


async def run_web_search(query: str, engines: list[str], *, count: int) -> dict[str, Any]:
    """Обойти выбранные службы и склеить ссылки. Сбой одной не прячет ответ другой."""
    tasks = []
    if "bing" in engines:
        tasks.append(search_bing(query, count=count))
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
