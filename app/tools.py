from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx2 as httpx
from sqlalchemy import func, select

from app import storage
from app.config import get_settings
from app.db import Download, GeneratedImage
from app.model_providers.openrouter import generate_image as openrouter_generate_image
from app.notes import (
    NOTE_TOO_LARGE,
    latest_note_for_conversation,
    owned_conversation,
    parse_conversation_id,
    upsert_conversation_note,
)
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

_READ_NOTE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_chat_note",
        "description": (
            "Прочитать markdown-заметку текущего чата (заголовок и тело). "
            "Вызывай, когда нужно опереться на конспект, план или черновик, который "
            "пользователь ведёт в панели заметки. Не выдумывай содержимое — сначала прочитай."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_WRITE_NOTE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "write_chat_note",
        "description": (
            "Записать markdown в заметку текущего чата. "
            "mode=replace полностью заменяет тело; mode=append дописывает в конец. "
            "Вызывай, когда пользователь просит сохранить конспект, план, чеклист "
            "или правку в заметку."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "description": "Markdown-текст заметки.",
                },
                "title": {
                    "type": "string",
                    "description": "Заголовок заметки (необязательно).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["replace", "append"],
                    "description": (
                        "replace — заменить тело, append — дописать. По умолчанию replace."
                    ),
                },
            },
            "required": ["body"],
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

_IMAGE_ASPECT_RATIOS = frozenset(
    {"1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16", "21:9", "auto"}
)
_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_MAX_PROMPT = 4000

_GENERATE_IMAGE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "Сгенерировать изображение по текстовому описанию и вернуть публичный URL. "
            "Вызывай только когда пользователь явно просит нарисовать, сгенерировать "
            "картинку, иллюстрацию, обложку или фото. Не вызывай для метафор "
            "(«представь картину», «опиши образ») и если запрос двусмысленный — "
            "тогда сначала переспроси текстом, без этого инструмента. "
            "В ответе пользователю вставь markdown из поля markdown как есть."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Подробное описание картинки на языке пользователя: сцена, стиль, "
                        "свет, композиция."
                    ),
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": (
                        "Соотношение сторон, если пользователь его указал. "
                        "Допустимо: 1:1, 4:3, 3:4, 3:2, 2:3, 16:9, 9:16, 21:9, auto."
                    ),
                },
            },
            "required": ["prompt"],
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
    """Tools бота: дата, скачивание и заметка чата всегда; pzz, погода, картинки — по настройкам."""
    settings = get_settings()
    tools = [_DATETIME_TOOL, _DOWNLOAD_TOOL, _READ_NOTE_TOOL, _WRITE_NOTE_TOOL]
    if settings.pzz_enabled:
        tools.extend([_PZZ_SEARCH_TOOL, _PZZ_ADDRESS_TOOL, _PZZ_ORDER_TOOL])
    if settings.openweather_api_key:
        tools.extend([_WEATHER_TOOL, _USER_LOCATION_TOOL])
    if settings.image_generation_enabled:
        tools.append(_GENERATE_IMAGE_TOOL)
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


def _chat_note_tool(
    name: str,
    arguments: str,
    user: Any = None,
    db: Any = None,
    conversation_id: Any = None,
) -> str:
    conv_id = parse_conversation_id(conversation_id)
    if conv_id is None or user is None or db is None:
        return json.dumps(
            {"error": "Заметка доступна только внутри сохранённого чата."},
            ensure_ascii=False,
        )
    conv = owned_conversation(db, user, conv_id)
    if conv is None:
        return json.dumps({"error": "Чат не найден."}, ensure_ascii=False)
    if name == "read_chat_note":
        note = latest_note_for_conversation(db, conv.id)
        if note is None:
            return json.dumps({"exists": False, "title": "", "body": ""}, ensure_ascii=False)
        return json.dumps(
            {"exists": True, "title": note.title, "body": note.body},
            ensure_ascii=False,
        )
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    body = args.get("body")
    if body is None:
        return json.dumps({"error": "Нужен параметр body."}, ensure_ascii=False)
    title = args.get("title")
    note, err = upsert_conversation_note(
        db,
        user,
        conv,
        title=str(title) if title is not None else None,
        body=str(body),
        mode=str(args.get("mode") or "replace"),
    )
    if err == NOTE_TOO_LARGE:
        return json.dumps({"error": err}, ensure_ascii=False)
    if err or note is None:
        return json.dumps({"error": err or "Не удалось сохранить заметку."}, ensure_ascii=False)
    db.commit()
    db.refresh(note)
    return json.dumps(
        {"ok": True, "title": note.title, "body": note.body},
        ensure_ascii=False,
    )


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


async def call_tool(
    name: str,
    arguments: str,
    user: Any = None,
    db: Any = None,
    conversation_id: Any = None,
) -> str:
    """Исполнить инструмент и вернуть строковый результат для role:tool.

    Исполнение обёрнуто в OpenInference TOOL span (имя, аргументы и результат
    видны в Phoenix), когда трассировка включена.
    """
    with tool_span(name, arguments) as span:
        result = await _call_tool_impl(
            name, arguments, user=user, db=db, conversation_id=conversation_id
        )
        tool_output(span, result)
        return result


async def _call_tool_impl(
    name: str,
    arguments: str,
    user: Any = None,
    db: Any = None,
    conversation_id: Any = None,
) -> str:
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
    if name in {"read_chat_note", "write_chat_note"}:
        return _chat_note_tool(name, arguments, user=user, db=db, conversation_id=conversation_id)
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    if name == "generate_image":
        return await _generate_image(args, user=user, db=db)
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


def _mime_extension(media_type: str) -> str:
    mime = (media_type or "").split(";")[0].strip().lower()
    if mime in {"image/jpeg", "image/jpg"}:
        return "jpg"
    if mime == "image/webp":
        return "webp"
    return "png"


def _prompt_alt(prompt: str) -> str:
    text = " ".join(prompt.split())
    if len(text) > 80:
        text = text[:77].rstrip() + "…"
    return text.replace("[", "(").replace("]", ")") or "image"


def _generate_image_fail(user_id: Any, reason: str, **fields: Any) -> str:
    extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    if extras:
        logger.warning("generate_image failed user_id=%s reason=%s %s", user_id, reason, extras)
    else:
        logger.warning("generate_image failed user_id=%s reason=%s", user_id, reason)
    payload: dict[str, Any] = {"error": reason}
    status = fields.get("status")
    if status is not None:
        payload["status"] = status
    return json.dumps(payload, ensure_ascii=False)


async def _generate_image(args: dict[str, Any], user: Any = None, db: Any = None) -> str:
    settings = get_settings()
    user_id = getattr(user, "id", None)
    if not settings.image_generation_enabled:
        return _generate_image_fail(
            user_id, "Генерация изображений не настроена (нужны OpenRouter и S3)."
        )
    if user is None or db is None:
        return _generate_image_fail(user_id, "Нет контекста пользователя.")

    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return _generate_image_fail(user_id, "Нужен prompt — описание картинки.")
    if len(prompt) > _IMAGE_MAX_PROMPT:
        return _generate_image_fail(
            user_id, f"Слишком длинный prompt (максимум {_IMAGE_MAX_PROMPT} символов)."
        )

    aspect_raw = str(args.get("aspect_ratio") or "").strip()
    aspect_ratio = aspect_raw or None
    if aspect_ratio and aspect_ratio not in _IMAGE_ASPECT_RATIOS:
        allowed = ", ".join(sorted(_IMAGE_ASPECT_RATIOS))
        return _generate_image_fail(
            user_id,
            f"Недопустимый aspect_ratio. Используй одно из: {allowed}",
            aspect_ratio=aspect_ratio,
        )

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar(
        select(func.count())
        .select_from(GeneratedImage)
        .where(
            GeneratedImage.user_id == user.id,
            GeneratedImage.created_at >= start,
        )
    )
    if int(used or 0) >= settings.image_generation_daily_limit:
        return _generate_image_fail(
            user_id,
            (
                f"Дневной лимит генерации изображений исчерпан: "
                f"{settings.image_generation_daily_limit} в сутки."
            ),
            used=int(used or 0),
        )

    logger.info(
        "generate_image started user_id=%s model=%s aspect_ratio=%s prompt_chars=%s",
        user_id,
        settings.image_generation_model,
        aspect_ratio,
        len(prompt),
    )
    try:
        result = await openrouter_generate_image(prompt, aspect_ratio=aspect_ratio)
        if result.get("error"):
            return _generate_image_fail(
                user_id,
                str(result["error"]),
                status=result.get("status"),
                stage="openrouter",
            )

        image_bytes: bytes = result["bytes"]
        if len(image_bytes) > _IMAGE_MAX_BYTES:
            return _generate_image_fail(
                user_id,
                f"Изображение слишком большое ({len(image_bytes)} байт).",
                bytes=len(image_bytes),
            )

        media_type = str(result.get("media_type") or "image/png")
        model = str(result.get("model") or settings.image_generation_model)
        key = storage.object_key(user.id, _mime_extension(media_type))
        storage.save_debug_copy(key, image_bytes)
        try:
            url = storage.put_bytes(key, image_bytes, media_type)
        except Exception as exc:
            logger.warning(
                "generate_image failed user_id=%s reason=%s stage=s3 error=%s",
                user_id,
                "Не удалось сохранить изображение",
                exc,
            )
            return json.dumps(
                {"error": f"Не удалось сохранить изображение: {exc}"},
                ensure_ascii=False,
            )

        cost = result.get("cost")
        cost_usd = None if cost is None else str(cost)
        row = GeneratedImage(
            user_id=user.id,
            object_key=key,
            public_url=url,
            prompt=prompt,
            model=model,
            mime=media_type.split(";")[0].strip() or "image/png",
            size_bytes=len(image_bytes),
            aspect_ratio=aspect_ratio,
            cost_usd=cost_usd,
        )
        db.add(row)
        db.commit()
        logger.info(
            "generate_image saved user_id=%s key=%s bytes=%s model=%s cost_usd=%s",
            user_id,
            key,
            len(image_bytes),
            model,
            cost_usd,
        )
        logger.debug("generate_image url=%s", url)
        alt = _prompt_alt(prompt)
        markdown = f"![{alt}]({url})"
        return json.dumps(
            {
                "url": url,
                "markdown": markdown,
                "model": model,
                "aspect_ratio": aspect_ratio,
                "cost_usd": cost_usd,
            },
            ensure_ascii=False,
        )
    except Exception:
        logger.exception("generate_image failed user_id=%s", user_id)
        try:
            db.rollback()
        except Exception:
            logger.debug("generate_image rollback failed", exc_info=True)
        return json.dumps(
            {"error": "Не удалось сгенерировать изображение."},
            ensure_ascii=False,
        )


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
