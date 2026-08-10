from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx2 as httpx

from app.config import get_settings

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


def enabled_tools() -> list[dict[str, Any]]:
    """Tools, доступные боту. Дата/время — всегда; погода — при наличии ключа."""
    settings = get_settings()
    tools = [_DATETIME_TOOL]
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
    if name == "get_current_datetime":
        return _current_datetime(arguments)
    if name != "get_weather":
        return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
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
