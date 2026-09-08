"""Неофициальный клиент публичного API pzz.by (Пицца Лисицца).

Сайт — Backbone SPA: меню читается с GET /api/v1/{category}, заказ идёт
через cookie-сессию (PHPSESSID), корзину и POST /api/v1/basket/save.
Официального партнёрского API нет; контракт может измениться.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx2 as httpx

PZZ_ORIGIN = "https://pzz.by"
API_PREFIX = "/api/v1"
PRICE_DIVISOR = 10_000
CATALOG_TTL_SEC = 600
STREETS_TTL_SEC = 3600
_MAX_CATALOG_PAGES = 20
# Как Backbone SPA на pzz.by: только корневые позиции меню, в порядке витрины.
CATEGORY_QUERY: dict[str, dict[str, str]] = {
    "pizzas": {
        "filter": "meal_only:0,parent_id:is:null",
        "order": "position:asc",
    },
    "snacks": {
        "filter": "meal_only:0,parent_id:is:null",
        "order": "position:asc",
    },
    "drinks": {
        "filter": "pizzeria_type:pizzeria,is_alcoholic:0",
        "order": "position:asc",
    },
    "warmers": {
        "filter": "pizzeria_type:pizzeria",
        "order": "position:asc",
    },
    "desserts": {
        "filter": "pizzeria_type:pizzeria",
        "order": "position:asc",
    },
    "sauces": {
        "filter": "pizzeria_type:pizzeria",
        "order": "position:asc",
    },
}

CATEGORIES: dict[str, str] = {
    "pizzas": "Пиццы",
    "snacks": "Закуски",
    "desserts": "Десерты",
    "drinks": "Напитки",
    "sauces": "Соусы",
    "warmers": "Горячие напитки",
}

# type в add-item — единственное число, как на сайте.
ADD_ITEM_TYPE = {
    "pizzas": "pizza",
    "snacks": "snack",
    "desserts": "dessert",
    "drinks": "drink",
    "sauces": "sauce",
    "warmers": "warmer",
}

PIZZA_SIZES = (
    ("pinsa", "Пинса"),
    ("thin", "Тонкое 36 см"),
    ("medium", "31 см"),
    ("big", "36 см"),
)

_HEADERS = {
    "User-Agent": "aichat-pzz/1.0 (+https://pzz.by)",
    "Accept": "application/json",
    "Referer": f"{PZZ_ORIGIN}/",
}

_catalog_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_streets_cache: tuple[float, list[dict[str, Any]]] | None = None


def price_byn(raw: Any) -> float | None:
    """Цены на pzz.by хранятся в целых ×10000 (339000 → 33.90 BYN)."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 1:
        return None
    return round(value / PRICE_DIVISOR, 2)


def _offers(category: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    if category == "pizzas":
        flags = {
            "pinsa": item.get("is_pinsa"),
            "thin": item.get("is_thin"),
            "medium": item.get("is_medium"),
            "big": item.get("is_big"),
        }
        for size, label in PIZZA_SIZES:
            if not flags.get(size):
                continue
            amount = price_byn(item.get(f"{size}_price"))
            if amount is None:
                continue
            offers.append({"size": size, "label": label, "price_byn": amount})
        return offers
    if category == "snacks":
        big = price_byn(item.get("big_price"))
        if big is not None:
            offers.append({"size": "big", "label": "Большая", "price_byn": big})
        if item.get("has_medium"):
            medium = price_byn(item.get("medium_price"))
            if medium is not None:
                offers.append({"size": "medium", "label": "Средняя", "price_byn": medium})
        return offers
    amount = price_byn(item.get("price") or item.get("big_price"))
    if amount is not None:
        offers.append({"size": "", "label": "", "price_byn": amount})
    return offers


def summarize_product(category: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if not item.get("in_stock", True) or item.get("is_hidden_for_menu"):
        return None
    offers = _offers(category, item)
    if not offers:
        return None
    return {
        "id": item.get("id"),
        "category": category,
        "title": item.get("title"),
        "description": (item.get("anonce") or item.get("short_description") or "").strip(),
        "offers": offers,
        "photo": item.get("photo_small") or item.get("photo1") or "",
        "url": f"{PZZ_ORIGIN}/{category}",
    }


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
        return payload["response"].get("data")
    return None


def _last_page(payload: Any) -> int | None:
    """Laravel-style pagination; None means the list is not paged (do not fetch page=2)."""
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    meta = response.get("meta")
    if not isinstance(meta, dict):
        return None
    raw = meta.get("last_page", meta.get("lastPage"))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


async def _get_json(client: httpx.AsyncClient, path: str, **kwargs: Any) -> Any:
    response = await client.get(path, **kwargs)
    try:
        return response.json()
    except Exception:
        return {"error": True, "code": response.status_code, "message": "invalid json"}


async def fetch_category(
    category: str, client: httpx.AsyncClient | None = None
) -> list[dict[str, Any]]:
    if category not in CATEGORIES:
        return []
    now = time.monotonic()
    cached = _catalog_cache.get(category)
    if cached and now - cached[0] < CATALOG_TTL_SEC:
        return cached[1]
    own = client is None
    if own:
        client = httpx.AsyncClient(base_url=PZZ_ORIGIN, timeout=20.0, headers=_HEADERS)
    try:
        query = dict(CATEGORY_QUERY.get(category) or {})
        items: list[dict[str, Any]] = []
        seen: set[Any] = set()
        page = 1
        last_page = 1
        while page <= last_page and page <= _MAX_CATALOG_PAGES:
            params = dict(query)
            if page > 1:
                params["page"] = str(page)
            payload = await _get_json(client, f"{API_PREFIX}/{category}", params=params)
            raw = _unwrap(payload)
            chunk = raw if isinstance(raw, list) else []
            for item in chunk:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if item_id is not None and item_id in seen:
                    continue
                if item_id is not None:
                    seen.add(item_id)
                items.append(item)
            detected = _last_page(payload)
            if detected is None:
                break
            last_page = detected
            if not chunk:
                break
            page += 1
        _catalog_cache[category] = (now, items)
        return items
    finally:
        if own:
            await client.aclose()


def clear_caches() -> None:
    _catalog_cache.clear()
    global _streets_cache
    _streets_cache = None


def _tokens(query: str) -> list[str]:
    return [part for part in re.split(r"\s+", query.lower().strip()) if part]


def _matches(item: dict[str, Any], tokens: list[str]) -> bool:
    hay = " ".join(
        str(item.get(key) or "") for key in ("title", "title_eng", "anonce", "short_description")
    ).lower()
    return all(token in hay for token in tokens)


async def search_menu(
    query: str = "", category: str | None = None, limit: int = 50
) -> dict[str, Any]:
    categories = [category] if category and category in CATEGORIES else list(CATEGORIES)
    tokens = _tokens(query)
    found: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=PZZ_ORIGIN, timeout=20.0, headers=_HEADERS) as client:
        for cat in categories:
            for item in await fetch_category(cat, client):
                if tokens and not _matches(item, tokens):
                    continue
                summary = summarize_product(cat, item)
                if summary:
                    found.append(summary)
    items = found[: max(1, limit)]
    note = (
        "Цены в белорусских рублях (BYN), как на pzz.by. "
        "Перед заказом уточни размер пиццы (пинса / тонкое / 31 см / 36 см)."
    )
    if len(found) > len(items):
        note += f" Показаны {len(items)} из {len(found)} позиций — уточни запрос или категорию."
    return {
        "source": "pzz.by",
        "query": query,
        "categories": {key: CATEGORIES[key] for key in categories},
        "count": len(items),
        "total": len(found),
        "items": items,
        "menu_url": PZZ_ORIGIN,
        "note": note,
    }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))


async def fetch_streets(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    global _streets_cache
    now = time.monotonic()
    if _streets_cache and now - _streets_cache[0] < STREETS_TTL_SEC:
        return _streets_cache[1]
    own = client is None
    if own:
        client = httpx.AsyncClient(base_url=PZZ_ORIGIN, timeout=20.0, headers=_HEADERS)
    try:
        payload = await _get_json(client, f"{API_PREFIX}/streets")
        raw = _unwrap(payload)
        streets = raw if isinstance(raw, list) else []
        _streets_cache = (now, streets)
        return streets
    finally:
        if own:
            await client.aclose()


def _pick_street(streets: list[dict[str, Any]], street: str) -> dict[str, Any] | None:
    needle = _norm(street)
    if not needle:
        return None
    exact = [row for row in streets if _norm(str(row.get("title") or "")) == needle]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return exact[0]
    partial = [row for row in streets if needle in _norm(str(row.get("title") or ""))]
    if len(partial) == 1:
        return partial[0]
    if partial:
        # Самое короткое название обычно точнее («Независимости просп.» vs длинные).
        return sorted(partial, key=lambda row: len(str(row.get("title") or "")))[0]
    return None


async def lookup_address(street: str, house: str = "") -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=PZZ_ORIGIN, timeout=20.0, headers=_HEADERS) as client:
        streets = await fetch_streets(client)
        match = _pick_street(streets, street)
        if not match:
            suggestions = [
                {"id": row.get("id"), "title": row.get("title")}
                for row in streets
                if _norm(street) and _norm(street) in _norm(str(row.get("title") or ""))
            ][:8]
            return {
                "error": "Улица не найдена в справочнике pzz.by.",
                "suggestions": suggestions,
                "hint": "Укажи улицу как на pzz.by, например «Независимости просп.».",
            }
        result: dict[str, Any] = {
            "street_id": match.get("id"),
            "street_title": match.get("title"),
            "source": "pzz.by",
        }
        house_q = _norm(house)
        if not house_q:
            result["need"] = "house"
            result["message"] = "Улица найдена. Укажи номер дома для проверки зоны доставки."
            return result
        payload = await _get_json(
            client,
            f"{API_PREFIX}/streets/{match['id']}",
            params={"order": "title:asc"},
        )
        houses = _unwrap(payload)
        houses = houses if isinstance(houses, list) else []
        house_row = next(
            (row for row in houses if _norm(str(row.get("title") or "")) == house_q),
            None,
        )
        if house_row is None:
            close = [
                row.get("title")
                for row in houses
                if str(row.get("title") or "").startswith(house.strip())
            ][:10]
            return {
                "error": "Дом не найден в справочнике этой улицы.",
                "street_id": match.get("id"),
                "street_title": match.get("title"),
                "house_suggestions": close,
            }
        result.update(
            {
                "house_id": house_row.get("id"),
                "house_title": house_row.get("title"),
                "delivery_available": bool(house_row.get("pizzeria_active")),
                "to_entrance_only": bool(house_row.get("to_entrance")),
                "public_comment": house_row.get("public_comment") or "",
            }
        )
        return result


def normalize_phone(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("80") and len(digits) == 11:
        digits = "375" + digits[2:]
    if digits.startswith("375") and len(digits) == 12:
        rest = digits[3:]
    elif len(digits) == 9:
        rest = digits
    else:
        return None
    if not re.match(r"^(17|25|29|33|44)\d{7}$", rest):
        return None
    return "+375" + rest


def _default_size(summary: dict[str, Any], requested: str) -> dict[str, Any] | None:
    offers = summary.get("offers") or []
    if not offers:
        return None
    want = (requested or "").strip().lower()
    aliases = {
        "большая": "big",
        "big": "big",
        "36": "big",
        "тонкая": "thin",
        "тонкое": "thin",
        "thin": "thin",
        "средняя": "medium",
        "medium": "medium",
        "31": "medium",
        "пинса": "pinsa",
        "pinsa": "pinsa",
    }
    size = aliases.get(want, want)
    if size:
        for offer in offers:
            if offer.get("size") == size or offer.get("label", "").lower() == want:
                return offer
    return offers[0]


async def _resolve_items(raw_items: list[Any]) -> tuple[list[dict[str, Any]], str | None]:
    if not raw_items:
        return [], "Добавь хотя бы одну позицию (id + category, опционально size и quantity)."
    resolved: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=PZZ_ORIGIN, timeout=20.0, headers=_HEADERS) as client:
        for raw in raw_items:
            if not isinstance(raw, dict):
                return [], "Каждая позиция заказа должна быть объектом."
            category = str(raw.get("category") or "").strip()
            if category not in CATEGORIES:
                return (
                    [],
                    f"Неизвестная категория {category!r}. Допустимы: {', '.join(CATEGORIES)}.",
                )
            try:
                product_id = int(raw.get("id"))
            except (TypeError, ValueError):
                return [], "У позиции должен быть числовой id из pzz_search_menu."
            try:
                quantity = int(raw.get("quantity") or 1)
            except (TypeError, ValueError):
                quantity = 1
            if quantity < 1 or quantity > 20:
                return [], "quantity должен быть от 1 до 20."
            product = None
            for item in await fetch_category(category, client):
                if int(item.get("id") or 0) == product_id:
                    product = summarize_product(category, item)
                    raw_item = item
                    break
            else:
                raw_item = {}
            if not product:
                return [], f"Товар {category} id={product_id} не найден или не в наличии."
            offer = _default_size(product, str(raw.get("size") or ""))
            if not offer:
                return [], f"Нет доступного размера для {product['title']}."
            resolved.append(
                {
                    "id": product_id,
                    "category": category,
                    "type": ADD_ITEM_TYPE[category],
                    "title": product["title"],
                    "size": offer.get("size") or "",
                    "size_label": offer.get("label") or "",
                    "price_byn": offer["price_byn"],
                    "quantity": quantity,
                    "line_total_byn": round(offer["price_byn"] * quantity, 2),
                    "photo": product.get("photo") or "",
                    "raw": raw_item,
                }
            )
    return resolved, None


def quote_payload(
    items: list[dict[str, Any]],
    address: dict[str, Any] | None = None,
    contact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total = round(sum(item["line_total_byn"] for item in items), 2)
    public_items = [{k: v for k, v in item.items() if k != "raw"} for item in items]
    payload: dict[str, Any] = {
        "source": "pzz.by",
        "items": public_items,
        "total_byn": total,
        "checkout_url": PZZ_ORIGIN,
        "payment_hint": (
            "На сайте доступны наличные, карта курьеру и онлайн (bePaid). "
            "Через чат отправляется только оплата наличными курьеру."
        ),
    }
    if address:
        payload["address"] = {k: v for k, v in address.items() if k != "error"}
    if contact:
        payload["contact"] = contact
    return payload


async def preview_order(args: dict[str, Any]) -> dict[str, Any]:
    items, err = await _resolve_items(list(args.get("items") or []))
    if err:
        return {"error": err}
    street = str(args.get("street") or "").strip()
    house = str(args.get("house") or "").strip()
    address = None
    if street:
        address = await lookup_address(street, house)
        if address.get("error"):
            return address
    name = str(args.get("name") or "").strip()
    phone = normalize_phone(str(args.get("phone") or ""))
    contact = {"name": name, "phone": phone or str(args.get("phone") or "")}
    quote = quote_payload(items, address, contact)
    missing: list[str] = []
    if not name:
        missing.append("name")
    if not phone:
        missing.append("phone (+375 25/29/33/44/17…)")
    if not street or not house:
        missing.append("street+house")
    if address and not address.get("delivery_available", True):
        quote["warning"] = "По этому дому доставка может быть недоступна."
    quote["missing"] = missing
    quote["confirm_required"] = True
    quote["message"] = (
        "Это черновик заказа по данным pzz.by. Покажи состав и сумму пользователю. "
        "Отправка на кухню — только после явного согласия и вызова "
        "pzz_place_order с confirm=true. Имя, телефон и адрес уйдут на pzz.by."
    )
    return quote


def _add_item_form(item: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": item["type"],
        "id": item["id"],
        "size": item["size"],
        "dough": "thin" if item["category"] == "pizzas" else "",
        "title": item["title"],
        "without_onion": 0,
    }
    raw_price = None
    raw = item.get("raw") or {}
    if item["category"] == "pizzas" and item["size"]:
        raw_price = raw.get(f"{item['size']}_price")
    else:
        raw_price = raw.get("price") or raw.get("big_price")
    if raw_price is not None:
        data["price"] = raw_price
    return data


async def place_order(args: dict[str, Any], *, orders_enabled: bool) -> dict[str, Any]:
    preview = await preview_order(args)
    if preview.get("error") or preview.get("missing"):
        if preview.get("missing"):
            preview.setdefault(
                "error",
                "Не хватает данных для отправки: " + ", ".join(preview["missing"]),
            )
        return preview
    if not args.get("confirm"):
        preview["submitted"] = False
        preview["message"] = (
            "Пользователь ещё не подтвердил заказ. Спроси согласие на отправку "
            "на pzz.by (наличные курьеру), затем вызови инструмент снова с confirm=true."
        )
        return preview
    if not orders_enabled:
        preview["submitted"] = False
        preview["error"] = (
            "Отправка заказа на pzz.by выключена (PZZ_ORDERS_ENABLED). "
            "Пользователь может оформить тот же состав на https://pzz.by"
        )
        return preview

    address = preview["address"]
    phone = preview["contact"]["phone"]
    name = preview["contact"]["name"]
    items, _ = await _resolve_items(list(args.get("items") or []))

    form = {
        "name": name,
        "phone": phone,
        "phonePrefix": "+375",
        "phoneNumber": phone.replace("+375", ""),
        "street": address.get("street_title") or args.get("street"),
        "house": address.get("house_title") or args.get("house"),
        "flat": str(args.get("flat") or ""),
        "entrance": str(args.get("entrance") or ""),
        "floor": str(args.get("floor") or ""),
        "intercom": str(args.get("intercom") or ""),
        "comment": str(args.get("comment") or ""),
        "payment": "cash",
        "is_confirm_privacy_policy_rules": 1,
        "no_contact_delivery": 0,
    }

    async with httpx.AsyncClient(
        base_url=PZZ_ORIGIN,
        timeout=30.0,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        await client.get("/")
        house_id = address.get("house_id")
        if house_id:
            await client.post(f"{API_PREFIX}/basket/house", data={"house_id": house_id})
        for item in items:
            for _ in range(item["quantity"]):
                posted = await client.post(
                    f"{API_PREFIX}/basket/add-item",
                    data=_add_item_form(item),
                )
                try:
                    body = posted.json()
                except Exception:
                    return {
                        "error": "pzz.by не принял позицию корзины.",
                        "status": posted.status_code,
                    }
                if isinstance(body, dict) and body.get("error"):
                    return {
                        "error": "Не удалось добавить товар в корзину pzz.by.",
                        "detail": body,
                    }
        updated = await client.post(f"{API_PREFIX}/basket/update-address", data=form)
        try:
            updated_body = updated.json()
        except Exception:
            updated_body = {}
        warning = None
        if isinstance(updated_body, dict):
            warning = ((updated_body.get("response") or {}).get("data") or {}).get("warning")
        if warning:
            return {"error": str(warning), "submitted": False, "quote": preview}

        saved = await client.post(f"{API_PREFIX}/basket/save")
        try:
            saved_body = saved.json()
        except Exception:
            return {"error": "Нет JSON-ответа от pzz.by при сохранении заказа."}
        data = (
            (saved_body.get("response") or {}).get("data") if isinstance(saved_body, dict) else None
        )
        if not isinstance(data, dict) or not data.get("num"):
            return {
                "error": "pzz.by не подтвердил заказ.",
                "detail": saved_body,
                "submitted": False,
            }
        payment = data.get("payment")
        result = {
            "submitted": True,
            "source": "pzz.by",
            "order_num": data.get("num"),
            "order_sync": data.get("sync"),
            "total_byn": price_byn(data.get("total")) or preview.get("total_byn"),
            "payment": payment,
            "message": (
                f"Заказ принят pzz.by, номер {data.get('num')}. " "Оплата наличными курьеру."
            ),
        }
        if payment in ("online", "krok") and data.get("bepaid_redirect_url"):
            result["payment_url"] = data.get("bepaid_redirect_url")
            result["message"] = (
                f"Заказ {data.get('num')} создан, но нужна онлайн-оплата. "
                "Открой ссылку bePaid в браузере."
            )
        return result
