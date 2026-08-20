from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx2 as httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import Download
from app.pravo import get_document as pravo_get_document
from app.pravo import search_register as pravo_search_register
from app.pzz import lookup_address, place_order, search_menu
from app.telemetry import tool_output, tool_span

logger = logging.getLogger("aichat.tools")

_RU_WEEKDAYS = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]

_DATETIME_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_current_datetime",
        "description": (
            "Узнать текущие дату и время. Вызывай, когда нужны актуальные дата, день недели "
            "или время (например, «какой сегодня день», «сколько сейчас времени»). "
            "Необязательный параметр timezone — IANA-имя часового пояса "
            "(например, Europe/Minsk). Без него возвращается серверное время."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA-название часового пояса, например Europe/Minsk.",
                },
            },
        },
    },
}

_WEATHER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "Узнать текущую погоду. Укажи город (по-русски или по-английски) ЛИБО "
            "координаты lat и lon (например, когда пользователь поделился геолокацией). "
            "КРИТИЧЕСКИ ВАЖНО: в итоговом ответе пользователю обязательно начни с фразы "
            "'По данным сервиса OpenWeatherMap:' или 'Согласно данным OpenWeatherMap: '."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Название города, например «Минск» или «London».",
                },
                "lat": {
                    "type": "number",
                    "description": "Широта, если локация известна по координатам.",
                },
                "lon": {
                    "type": "number",
                    "description": "Долгота, если локация известна по координатам.",
                },
            },
        },
    },
}

_USER_LOCATION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_user_location",
        "description": (
            "Запросить у пользователя его местоположение через браузер. Вызывай, когда "
            "нужна погода, но город или координаты не указаны в вопросе."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

_DOWNLOAD_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "download_file",
        "description": (
            "Скачать страницу или файл по URL и вернуть его содержимое. Вызывай, когда "
            "пользователь просит посмотреть, что находится по ссылке, прочитать текст "
            "страницы или документа. Скачиваются только текстовые файлы размером до 2 МБ; "
            "повторные запросы одного и того же URL не перекачиваются заново."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Полный http/https адрес, например https://example.com/page",
                },
            },
            "required": ["url"],
        },
    },
}

_PRAVO_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "pravo_search",
        "description": (
            "Поиск правовых актов Республики Беларусь на портале pravo.by "
            "(Национальный реестр и каталог кодексов). Вызывай при юридических "
            "вопросах по законодательству РБ: законы, указы, кодексы, постановления. "
            "query — название или тема; registry_number — номер в реестре вроде 2/1742. "
            "Это официальное опубликование, не гарантированно сводная действующая редакция "
            "и не юридическая консультация. В ответе пользователю обязательно начни с "
            "'По данным pravo.by:' и приложи ссылки. После поиска читай карточку или HTML "
            "через pravo_get_document (только URL на pravo.by)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Название или тема, например «Трудовой кодекс» "
                        "или «географических объектов»."
                    ),
                },
                "registry_number": {
                    "type": "string",
                    "description": (
                        "Регистрационный номер в реестре, например 2/1742 или 6-4/55020."
                    ),
                },
                "page": {
                    "type": "integer",
                    "description": "Страница результатов, с 1. По умолчанию 1.",
                },
            },
        },
    },
}

_PRAVO_DOCUMENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "pravo_get_document",
        "description": (
            "Открыть документ pravo.by по URL из pravo_search: карточка реестра "
            "(реквизиты, ссылка на PDF) или HTML-текст публикации. "
            "Не ходи на etalonline.by этим инструментом. "
            "Необязательный query — найти сниппеты («статья 42», формулировка нормы). "
            "В ответе пользователю начни с 'По данным pravo.by:'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Полный https://pravo.by/document/... адрес.",
                },
                "query": {
                    "type": "string",
                    "description": "Фрагмент для поиска в тексте HTML-публикации.",
                },
            },
            "required": ["url"],
        },
    },
}

_PZZ_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "description": "id товара из pzz_search_menu"},
        "category": {
            "type": "string",
            "enum": ["pizzas", "snacks", "desserts", "drinks", "sauces", "warmers"],
        },
        "size": {
            "type": "string",
            "description": "Для пиццы: pinsa, thin, medium, big. Для закусок: big/medium.",
        },
        "quantity": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "required": ["id", "category"],
}

_PZZ_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "pzz_search_menu",
        "description": (
            "Ассортимент Пиццы Лисицца (pzz.by): поиск по названию или категории. "
            "Вызывай, когда пользователь хочет пиццу, закуски, напитки с pzz.by. "
            "В ответе пользователю укажи, что данные с pzz.by, и цены в BYN."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Что ищем, например «пепперони» или «морс». Можно пусто.",
                },
                "category": {
                    "type": "string",
                    "enum": ["pizzas", "snacks", "desserts", "drinks", "sauces", "warmers"],
                    "description": "Ограничить раздел меню.",
                },
            },
        },
    },
}

_PZZ_ADDRESS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "pzz_lookup_address",
        "description": (
            "Проверить улицу и дом в зоне доставки pzz.by (Минск и окрестности). "
            "Вызывай перед оформлением заказа."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "street": {"type": "string", "description": "Улица, как на pzz.by."},
                "house": {"type": "string", "description": "Номер дома, например «10» или «10А»."},
            },
            "required": ["street"],
        },
    },
}

_PZZ_ORDER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "pzz_place_order",
        "description": (
            "Собрать или отправить заказ в Пиццу Лисицца (pzz.by). "
            "Сначала вызывай с confirm=false (или без confirm), покажи состав и сумму. "
            "confirm=true — ТОЛЬКО после явного согласия пользователя: состав, адрес, "
            "телефон и оплата наличными курьеру. Имя/телефон/адрес уходят на pzz.by. "
            "Не выдумывай id товаров — бери их из pzz_search_menu."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": _PZZ_ITEM},
                "name": {"type": "string", "description": "Имя получателя."},
                "phone": {
                    "type": "string",
                    "description": "Мобильный РБ, например +375297556655.",
                },
                "street": {"type": "string"},
                "house": {"type": "string"},
                "flat": {"type": "string"},
                "entrance": {"type": "string"},
                "floor": {"type": "string"},
                "intercom": {"type": "string"},
                "comment": {"type": "string"},
                "confirm": {
                    "type": "boolean",
                    "description": "true только после явного согласия пользователя.",
                },
            },
            "required": ["items"],
        },
    },
}

# Допустимые текстовые MIME-типы (whitelist) + эвристики для неизвестных типов.
_TEXT_MIMES = frozenset(
    {
        "text/plain",
        "text/html",
        "text/markdown",
        "text/x-markdown",
        "text/csv",
        "text/xml",
        "text/x-yaml",
        "text/yaml",
        "text/javascript",
        "text/css",
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "application/javascript",
        "application/x-javascript",
        "application/rss+xml",
        "application/atom+xml",
        "application/x-yaml",
        "application/yaml",
    }
)
_TEXT_SNIFF_BYTES = 8192
_MAX_CONTENT_CHARS = 50 * 1024
_MAX_REDIRECTS = 5
_USER_AGENT = "aichat/1.0 (+download tool)"

# Сети, к которым нельзя ходить из download_file (SSRF-защита):
# приватные, loopback, link-local, резервные, документационные, multicast.
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),
]


def enabled_tools() -> list[dict[str, Any]]:
    """Tools, доступные боту. Дата/время и скачивание — всегда; pravo/pzz/погода — по настройкам."""
    settings = get_settings()
    tools = [_DATETIME_TOOL, _DOWNLOAD_TOOL]
    if settings.pravo_enabled:
        tools.extend([_PRAVO_SEARCH_TOOL, _PRAVO_DOCUMENT_TOOL])
    if settings.pzz_enabled:
        tools.extend([_PZZ_SEARCH_TOOL, _PZZ_ADDRESS_TOOL, _PZZ_ORDER_TOOL])
    if settings.openweather_api_key:
        tools.extend([_WEATHER_TOOL, _USER_LOCATION_TOOL])
    return tools


def extract_tool_calls(sse_raw: bytes) -> list[dict[str, str]]:
    """Собрать tool_calls из дельт OpenAI-совместимого SSE-стрима.

    Возвращает список отсортированных по индексу вызовов
    вида {"id", "name", "arguments"}.
    """
    calls: dict[int, dict[str, str]] = {}
    for line in sse_raw.split(b"\n"):
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            continue
        data = stripped[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        deltas = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("tool_calls")
        if not deltas:
            continue
        for delta in deltas:
            if not isinstance(delta, dict):
                continue
            index = int(delta.get("index", 0))
            entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if delta.get("id"):
                entry["id"] = delta["id"]
            fn = delta.get("function") or {}
            if fn.get("name"):
                entry["name"] += fn["name"]
            if fn.get("arguments"):
                entry["arguments"] += fn["arguments"]
    return [calls[index] for index in sorted(calls)]


async def _pravo_tool(name: str, args: dict[str, Any]) -> str:
    settings = get_settings()
    if not settings.pravo_enabled:
        return json.dumps({"error": "Поиск pravo.by выключен."}, ensure_ascii=False)
    if name == "pravo_search":
        page_raw = args.get("page", 1)
        try:
            page = int(page_raw)
        except (TypeError, ValueError):
            page = 1
        payload = await pravo_search_register(
            query=str(args.get("query") or ""),
            registry_number=str(args.get("registry_number") or ""),
            page=page,
        )
    else:
        payload = await pravo_get_document(
            str(args.get("url") or ""),
            query=str(args.get("query") or ""),
        )
    return json.dumps(payload, ensure_ascii=False)


async def _pzz_tool(name: str, args: dict[str, Any]) -> str:
    settings = get_settings()
    if not settings.pzz_enabled:
        return json.dumps({"error": "Заказы pzz.by выключены."}, ensure_ascii=False)
    if name == "pzz_search_menu":
        payload = await search_menu(
            query=str(args.get("query") or ""),
            category=str(args.get("category") or "") or None,
        )
    elif name == "pzz_lookup_address":
        payload = await lookup_address(
            str(args.get("street") or ""),
            str(args.get("house") or ""),
        )
    else:
        payload = await place_order(args, orders_enabled=settings.pzz_orders_enabled)
    return json.dumps(payload, ensure_ascii=False)


async def call_tool(name: str, arguments: str, user: Any = None, db: Any = None) -> str:
    """Исполнить инструмент и вернуть строковый результат для role:tool.

    Исполнение обёрнуто в OpenInference TOOL span (имя, аргументы и результат
    видны в Phoenix), когда трассировка включена.
    """
    with tool_span(name, arguments) as span:
        result = await _call_tool_impl(name, arguments, user=user, db=db)
        tool_output(span, result)
        return result


async def _call_tool_impl(name: str, arguments: str, user: Any = None, db: Any = None) -> str:
    """Реализация инструментов; см. :func:`call_tool`."""
    if name == "get_user_location":
        return json.dumps(
            {"error": "Местоположение запрашивается у пользователя на клиенте."},
            ensure_ascii=False,
        )
    if name == "get_current_datetime":
        return _current_datetime(arguments)
    if name == "download_file":
        return await _download_file(arguments, user=user, db=db)
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    if name in {"pravo_search", "pravo_get_document"}:
        return await _pravo_tool(name, args)
    if name in {"pzz_search_menu", "pzz_lookup_address", "pzz_place_order"}:
        return await _pzz_tool(name, args)
    if name != "get_weather":
        return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    city = str(args.get("city") or "").strip()
    lat = args.get("lat")
    lon = args.get("lon")
    if not city and lat is None and lon is None:
        return json.dumps(
            {
                "error": (
                    "Не указаны город или координаты. Если локация неизвестна — "
                    "вызови get_user_location."
                )
            },
            ensure_ascii=False,
        )
    return await _weather(city, lat, lon)


def _current_datetime(arguments: str) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}

    tz_name = str(args.get("timezone") or "").strip()
    if tz_name:
        try:
            now = datetime.now(ZoneInfo(tz_name))
        except ZoneInfoNotFoundError:
            return json.dumps({"error": f"Неизвестный часовой пояс: {tz_name}"}, ensure_ascii=False)
    else:
        now = datetime.now().astimezone()

    return json.dumps(
        {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": _RU_WEEKDAYS[now.weekday()],
            "timezone": str(now.tzinfo),
        },
        ensure_ascii=False,
    )


async def _weather(city: str, lat: Any = None, lon: Any = None) -> str:
    settings = get_settings()
    if not settings.openweather_api_key:
        return json.dumps({"error": "Погодный сервис не настроен."}, ensure_ascii=False)
    params: dict[str, Any] = {
        "appid": settings.openweather_api_key,
        "units": "metric",
        "lang": "ru",
    }
    if city:
        params["q"] = city
    else:
        params["lat"] = lat
        params["lon"] = lon
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            "https://api.openweathermap.org/data/2.5/weather", params=params
        )
    try:
        data = response.json()
    except Exception:
        return json.dumps({"error": "Погодный сервис недоступен."}, ensure_ascii=False)

    if response.status_code != 200 or data.get("cod") != 200:
        message = data.get("message", "город не найден") if isinstance(data, dict) else "ошибка"
        return json.dumps({"error": str(message)}, ensure_ascii=False)

    payload = {
        "city": f"{data.get('name', city)}, {data.get('sys', {}).get('country', '')}".strip(" ,"),
        "temperature_c": data.get("main", {}).get("temp"),
        "feels_like_c": data.get("main", {}).get("feels_like"),
        "humidity_percent": data.get("main", {}).get("humidity"),
        "wind_m_s": data.get("wind", {}).get("speed"),
        "description": data.get("weather", [{}])[0].get("description"),
    }
    return json.dumps(payload, ensure_ascii=False)


def _tool_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _download_error(message: str) -> str:
    return _tool_json({"error": message})


def _resolve_host(host: str) -> list[str]:
    """Разрешить хост во все IP-адреса (убирая IPv6 zone-id)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return list({info[4][0].split("%")[0] for info in infos})


def _is_blocked(addr: str) -> bool:
    """True для приватных / локальных адресов, к которым ходить нельзя."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    return any(ip in network for network in _PRIVATE_NETWORKS)


def _assert_public_url(url: str) -> str | None:
    """Вернуть текст ошибки, если URL ходить нельзя, иначе None.

    SSRF-защита: разрешаем все адреса хоста и блокируем, если любой из них
    приватный/локальный. Известное ограничение MVP: между проверкой и реальным
    connect остаётся окно для DNS-rebinding (хост переразрешается на каждом
    редиректе, но DNS может успевать менять ответы).
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return "Поддерживаются только http/https адреса."
    host = parsed.hostname
    if not host:
        return "Некорректный URL."
    addresses = _resolve_host(host)
    if not addresses:
        return "Не удалось разрешить имя хоста."
    blocked = [addr for addr in addresses if _is_blocked(addr)]
    if blocked:
        logger.warning("ssrf_blocked host=%s addresses=%s", host, blocked)
        return "Доступ к этому адресу запрещён (локальная сеть)."
    return None


def _is_text_content(content_type: str, data: bytes) -> bool:
    """Whitelist текстовых типов + эвристики для неизвестных."""
    if content_type in _TEXT_MIMES or content_type.startswith("text/"):
        return True
    if b"\x00" in data[:_TEXT_SNIFF_BYTES]:
        return False
    return content_type in ("", "application/octet-stream", "application/unknown")


def _filename_from_url(url: str, content_type: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    raw = urllib.parse.unquote(parsed.path)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(raw)).strip("._")
    if not name:
        name = "page.html" if content_type == "text/html" else "page.txt"
    if not Path(name).suffix:
        if content_type == "text/html":
            name += ".html"
        elif content_type == "application/json":
            name += ".json"
        else:
            name += ".txt"
    return name[:255]


def _unique_path(folder: Path, filename: str) -> Path:
    path = folder / filename
    stem, suffix = path.stem, path.suffix
    n = 1
    while path.exists():
        path = folder / f"{stem}-{n}{suffix}"
        n += 1
    return path


def _decode_text(data: bytes, content_type: str) -> str:
    charset = ""
    for param in content_type.split(";")[1:]:
        if "=" in param and param.split("=", 1)[0].strip().lower() == "charset":
            charset = param.split("=", 1)[1].strip().strip("\"'")
    for encoding in (charset or "utf-8", "utf-8", "latin-1"):
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _size_limit_message(settings) -> str:
    return f"Файл превышает лимит в {settings.downloads_max_bytes // (1024 * 1024)} МБ."


def _user_download_dir(user: Any) -> Path:
    settings = get_settings()
    digest = hashlib.sha256(str(user.id).encode("utf-8")).hexdigest()[:16]
    return settings.downloads_root / digest


async def _download_file(arguments: str, user: Any = None, db: Any = None) -> str:
    """Скачать URL, сохранить в data/customers/<hash(user_id)>/ и вернуть текст.

    Повторный запрос того же URL от того же пользователя отдаётся из кэша
    (таблица downloads в БД). Файл сохраняется только если это текст.
    """
    if user is None or db is None:
        return _download_error("Скачивание файлов недоступно.")
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    url = str(args.get("url") or "").strip()
    if not url:
        return _download_error("Укажите url для скачивания.")

    settings = get_settings()
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()

    existing = db.scalar(
        select(Download).where(Download.user_id == user.id, Download.url_hash == url_hash)
    )
    if existing is not None:
        path = Path(existing.file_path)
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="latin-1")
            truncated = len(text) > _MAX_CONTENT_CHARS
            return _tool_json(
                {
                    "filename": existing.filename,
                    "url": url,
                    "size_bytes": existing.size_bytes,
                    "cached": True,
                    "truncated": truncated,
                    "content": text[:_MAX_CONTENT_CHARS],
                }
            )

    folder = _user_download_dir(user)
    headers = {"User-Agent": _USER_AGENT}
    timeout = httpx.Timeout(30.0, connect=10.0)
    current = url
    final_url = url
    data = b""
    content_type = ""
    total = 0

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, headers=headers
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            error = _assert_public_url(current)
            if error:
                return _download_error(error)
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        return _download_error("Редирект без адреса Location.")
                    current = str(resp.url.join(location))
                    continue
                if resp.status_code >= 400:
                    return _download_error(f"Сервер вернул HTTP {resp.status_code}.")
                content_type = (
                    (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                )
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > settings.downloads_max_bytes:
                    return _download_error(_size_limit_message(settings))
                chunks: list[bytes] = []
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > settings.downloads_max_bytes:
                        return _download_error(_size_limit_message(settings))
                    chunks.append(chunk)
                data = b"".join(chunks)
                final_url = str(resp.url)
                break
        else:
            return _download_error("Слишком много редиректов.")

    if not _is_text_content(content_type, data):
        return _download_error(
            "Формат файла не поддерживается: допускаются только текстовые файлы."
        )

    filename = _filename_from_url(final_url, content_type)
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique_path(folder, filename)
    path.write_bytes(data)
    db.add(
        Download(
            user_id=user.id,
            url=url,
            url_hash=url_hash,
            filename=path.name,
            file_path=str(path),
            size_bytes=total,
        )
    )
    db.commit()

    text = _decode_text(data, content_type)
    truncated = len(text) > _MAX_CONTENT_CHARS
    return _tool_json(
        {
            "filename": path.name,
            "url": final_url,
            "size_bytes": total,
            "content_type": content_type,
            "truncated": truncated,
            "content": text[:_MAX_CONTENT_CHARS],
        }
    )
