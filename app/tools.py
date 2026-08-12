from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx

from app.config import get_settings
from app.pzz import lookup_address, place_order, search_menu

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


def enabled_tools() -> list[dict[str, Any]]:
    """Tools, доступные боту в /api/chat."""
    settings = get_settings()
    tools: list[dict[str, Any]] = []
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


async def call_tool(name: str, arguments: str) -> str:
    """Исполнить инструмент и вернуть строковый результат для role:tool.

    `get_user_location` исполняется не здесь: он запрашивает данные у браузера,
    поэтому маршрут обрабатывает его отдельно (см. app/routes/api.py).
    """
    if name == "get_user_location":
        return json.dumps(
            {"error": "Местоположение запрашивается у пользователя на клиенте."},
            ensure_ascii=False,
        )
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
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
