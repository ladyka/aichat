import asyncio
import json

from app.pzz import (
    clear_caches,
    fetch_category,
    fetch_streets,
    lookup_address,
    normalize_phone,
    place_order,
    preview_order,
    price_byn,
    quote_payload,
    search_menu,
    summarize_product,
)
from app.tools import call_tool

PIZZA = {
    "id": 35,
    "title": "Цыпленок дорблю",
    "title_eng": "Chicken Dorblu",
    "anonce": "филе цыпленка, сыр дорблю",
    "photo_small": "https://example.com/p.jpg",
    "in_stock": True,
    "is_hidden_for_menu": 0,
    "is_pinsa": True,
    "is_thin": 1,
    "is_medium": 1,
    "is_big": 1,
    "pinsa_price": 209000,
    "thin_price": 339000,
    "medium_price": 339000,
    "big_price": 405000,
}

STREETS = [{"id": 223, "title": "Независимости просп."}]
HOUSES = [
    {
        "id": 35087,
        "title": "1",
        "pizzeria_active": 1,
        "to_entrance": 1,
        "public_comment": "",
    }
]


class FakeResp:
    def __init__(self, data=None, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


def _ok(data):
    return FakeResp({"error": False, "code": 200, "response": {"data": data}})


SNACK = {
    "id": 10,
    "title": "Картофель фри",
    "anonce": "с соусом",
    "in_stock": True,
    "is_hidden_for_menu": 0,
    "big_price": 99000,
    "has_medium": 1,
    "medium_price": 69000,
}

DRINK = {
    "id": 20,
    "title": "Кола",
    "short_description": "0.5 л",
    "in_stock": True,
    "is_hidden_for_menu": 0,
    "price": 25000,
}

HIDDEN = {
    "id": 99,
    "title": "Скрытая",
    "in_stock": True,
    "is_hidden_for_menu": 1,
    "thin_price": 100000,
    "is_thin": 1,
}

OOS = {
    "id": 98,
    "title": "Нет в наличии",
    "in_stock": False,
    "is_thin": 1,
    "thin_price": 100000,
}

NO_PRICE = {
    "id": 97,
    "title": "Без цены",
    "in_stock": True,
    "is_thin": 1,
    "thin_price": 1,
}


class FakePzzClient:
    def __init__(self, *a, **kw):
        self.posts = []
        self.gets = []
        self.params = []
        self.catalog_pages = {}
        self.catalog = {
            "pizzas": [PIZZA, HIDDEN, OOS, NO_PRICE],
            "snacks": [SNACK],
            "desserts": [],
            "drinks": [DRINK],
            "sauces": [],
            "warmers": [],
        }
        self.streets = list(STREETS)
        self.houses = list(HOUSES)
        self.get_invalid = set()
        self.unwrap_invalid = set()
        self.add_item_resp = _ok({"price": 405000})
        self.save_resp = _ok({"num": 42, "sync": "abc", "total": 405000, "payment": "cash"})
        self.update_resp = _ok({})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        return None

    def _path(self, path):
        return str(path)

    def _category_from(self, path):
        for cat in self.catalog:
            if path.endswith(f"/{cat}") or path.endswith(f"/{cat}/"):
                return cat
        return None

    async def get(self, path, **kw):
        path = self._path(path)
        params = kw.get("params")
        self.gets.append(path)
        self.params.append(params)
        if path in ("/", "") or path.endswith("://pzz.by/"):
            return FakeResp(data=None)
        cat = self._category_from(path)
        if cat:
            if cat in self.get_invalid:
                return FakeResp(data=None, status_code=502)
            if cat in self.unwrap_invalid:
                return FakeResp({"error": False, "code": 200, "response": {"data": {"oops": 1}}})
            pages = self.catalog_pages.get(cat)
            if pages:
                page = 1
                if isinstance(params, dict) and params.get("page") not in (None, ""):
                    try:
                        page = int(params.get("page"))
                    except (TypeError, ValueError):
                        page = 1
                chunk = pages[page - 1] if 1 <= page <= len(pages) else []
                return FakeResp(
                    {
                        "error": False,
                        "code": 200,
                        "response": {
                            "data": chunk,
                            "meta": {"current_page": page, "last_page": len(pages)},
                        },
                    }
                )
            return _ok(self.catalog[cat])
        if "/streets/" in path:
            if "houses" in self.get_invalid:
                return FakeResp(data=None, status_code=502)
            return _ok(self.houses)
        if path.endswith("/streets") or path.endswith("/streets/"):
            if "streets" in self.get_invalid:
                return FakeResp(data=None, status_code=502)
            if "streets" in self.unwrap_invalid:
                return FakeResp({"not": "wrapped"})
            return _ok(self.streets)
        return FakeResp({"error": True, "code": 404}, status_code=404)

    async def post(self, path, **kw):
        path = self._path(path)
        data = kw.get("data") or {}
        self.posts.append((path, data))
        if path.endswith("/basket/save"):
            return self.save_resp
        if path.endswith("/basket/update-address"):
            return self.update_resp
        if path.endswith("/basket/add-item"):
            return self.add_item_resp
        if path.endswith("/basket/house"):
            return _ok({"price": 405000})
        return _ok({})


def _patch_pzz(monkeypatch):
    clear_caches()
    client = FakePzzClient()
    monkeypatch.setattr("app.pzz.httpx.AsyncClient", lambda *a, **kw: client)
    return client


def test_price_byn():
    assert price_byn(339000) == 33.9
    assert price_byn(1) is None
    assert price_byn(None) is None


def test_summarize_pizza_offers():
    summary = summarize_product("pizzas", PIZZA)
    assert summary["title"] == "Цыпленок дорблю"
    sizes = [row["size"] for row in summary["offers"]]
    assert sizes == ["pinsa", "thin", "medium", "big"]
    assert summary["offers"][-1]["price_byn"] == 40.5


def test_search_menu(monkeypatch):
    _patch_pzz(monkeypatch)
    result = json.loads(asyncio.run(call_tool("pzz_search_menu", '{"query": "дорблю"}')))
    assert result["count"] == 1
    assert result["items"][0]["id"] == 35
    assert result["source"] == "pzz.by"


def test_lookup_address(monkeypatch):
    _patch_pzz(monkeypatch)
    result = json.loads(
        asyncio.run(
            call_tool(
                "pzz_lookup_address",
                '{"street": "независимости", "house": "1"}',
            )
        )
    )
    assert result["house_id"] == 35087
    assert result["delivery_available"] is True


def test_place_order_requires_confirm(monkeypatch):
    _patch_pzz(monkeypatch)
    args = {
        "items": [{"id": 35, "category": "pizzas", "size": "big", "quantity": 1}],
        "name": "Иван",
        "phone": "+375297556655",
        "street": "Независимости просп.",
        "house": "1",
    }
    result = json.loads(asyncio.run(call_tool("pzz_place_order", json.dumps(args))))
    assert result["confirm_required"] is True
    assert result.get("submitted") is False
    assert result["total_byn"] == 40.5


def test_place_order_submits(monkeypatch):
    client = _patch_pzz(monkeypatch)
    args = {
        "items": [{"id": 35, "category": "pizzas", "size": "big", "quantity": 2}],
        "name": "Иван",
        "phone": "297556655",
        "street": "Независимости просп.",
        "house": "1",
        "confirm": True,
    }
    result = json.loads(asyncio.run(call_tool("pzz_place_order", json.dumps(args))))
    assert result["submitted"] is True
    assert result["order_num"] == 42
    save_posts = [path for path, _ in client.posts if str(path).endswith("/basket/save")]
    assert save_posts
    add_posts = [data for path, data in client.posts if str(path).endswith("/add-item")]
    assert len(add_posts) == 2
    assert add_posts[0]["id"] == 35
    assert add_posts[0]["type"] == "pizza"
    assert add_posts[0]["size"] == "big"


def test_place_order_disabled(monkeypatch):
    from app.config import get_settings

    _patch_pzz(monkeypatch)
    monkeypatch.setattr(get_settings(), "pzz_orders_enabled", False)
    args = {
        "items": [{"id": 35, "category": "pizzas", "size": "big"}],
        "name": "Иван",
        "phone": "+375297556655",
        "street": "Независимости просп.",
        "house": "1",
        "confirm": True,
    }
    result = json.loads(asyncio.run(call_tool("pzz_place_order", json.dumps(args))))
    assert result["submitted"] is False
    assert "PZZ_ORDERS_ENABLED" in result["error"]


def test_price_byn_invalid():
    assert price_byn("nope") is None
    assert price_byn(0) is None


def test_summarize_skips_hidden_oos_and_no_price():
    assert summarize_product("pizzas", HIDDEN) is None
    assert summarize_product("pizzas", OOS) is None
    assert summarize_product("pizzas", NO_PRICE) is None


def test_summarize_snack_and_drink():
    snack = summarize_product("snacks", SNACK)
    assert [row["size"] for row in snack["offers"]] == ["big", "medium"]
    drink = summarize_product("drinks", DRINK)
    assert drink["offers"][0]["price_byn"] == 2.5
    assert drink["description"] == "0.5 л"


def test_snack_without_medium():
    item = {**SNACK, "has_medium": 0}
    sizes = [row["size"] for row in summarize_product("snacks", item)["offers"]]
    assert sizes == ["big"]


def test_search_menu_category_and_limit(monkeypatch):
    _patch_pzz(monkeypatch)
    result = asyncio.run(search_menu(query="", category="snacks", limit=1))
    assert result["count"] == 1
    assert result["total"] == 1
    assert result["items"][0]["id"] == 10
    empty = asyncio.run(search_menu(query="неттакого", category="pizzas"))
    assert empty["count"] == 0
    unknown = asyncio.run(search_menu(category="nope"))
    assert "pizzas" in unknown["categories"]
    truncated = asyncio.run(search_menu(query="", limit=1))
    assert truncated["count"] == 1
    assert truncated["total"] > 1
    assert "из" in truncated["note"]


def test_fetch_pizzas_uses_menu_filters(monkeypatch):
    client = _patch_pzz(monkeypatch)
    asyncio.run(fetch_category("pizzas", client))
    assert client.params
    params = client.params[0] or {}
    assert "parent_id:is:null" in params.get("filter", "")
    assert params.get("order") == "position:asc"
    assert "page" not in params
    assert len([path for path in client.gets if path.endswith("/pizzas")]) == 1


def test_fetch_category_follows_pages(monkeypatch):
    client = _patch_pzz(monkeypatch)
    page1 = {**PIZZA, "id": 1, "title": "Первая страница"}
    page2 = {**PIZZA, "id": 2, "title": "Вторая страница"}
    client.catalog_pages = {"pizzas": [[page1], [page2]]}
    items = asyncio.run(fetch_category("pizzas", client))
    assert [row["id"] for row in items] == [1, 2]
    assert [row["title"] for row in items] == ["Первая страница", "Вторая страница"]
    assert len([path for path in client.gets if path.endswith("/pizzas")]) == 2


def test_fetch_unknown_category_and_invalid_json(monkeypatch):
    client = _patch_pzz(monkeypatch)
    assert asyncio.run(fetch_category("nope")) == []
    client.get_invalid.add("pizzas")
    items = asyncio.run(fetch_category("pizzas", client))
    assert items == []
    client.get_invalid.clear()
    client.unwrap_invalid.add("pizzas")
    clear_caches()
    items = asyncio.run(fetch_category("pizzas", client))
    assert items == []


def test_fetch_category_cache_and_own_client(monkeypatch):
    client = _patch_pzz(monkeypatch)
    first = asyncio.run(fetch_category("drinks"))
    assert first[0]["id"] == 20
    client.catalog["drinks"] = []
    cached = asyncio.run(fetch_category("drinks"))
    assert cached[0]["id"] == 20


def test_lookup_street_only_and_unknown(monkeypatch):
    _patch_pzz(monkeypatch)
    street_only = asyncio.run(lookup_address("Независимости просп.", ""))
    assert street_only["need"] == "house"
    missing = asyncio.run(lookup_address("Марса", "1"))
    assert "error" in missing
    no_house = asyncio.run(lookup_address("Независимости просп.", "999"))
    assert "Дом не найден" in no_house["error"]


def test_pick_street_partial_and_duplicates(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.streets = [
        {"id": 1, "title": "Независимости просп."},
        {"id": 2, "title": "Независимости ул. очень длинное название"},
        {"id": 3, "title": "Дубль"},
        {"id": 4, "title": "Дубль"},
    ]
    clear_caches()
    found = asyncio.run(lookup_address("независимости", "1"))
    assert found["street_id"] == 1
    dups = asyncio.run(lookup_address("Дубль", ""))
    assert dups["street_id"] == 3


def test_fetch_streets_invalid_and_cache(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.unwrap_invalid.add("streets")
    assert asyncio.run(fetch_streets(client)) == []
    client.unwrap_invalid.clear()
    clear_caches()
    first = asyncio.run(fetch_streets())
    assert first[0]["id"] == 223
    client.streets = []
    assert asyncio.run(fetch_streets())[0]["id"] == 223


def test_normalize_phone_variants():
    assert normalize_phone("80297556655") == "+375297556655"
    assert normalize_phone("+375 (29) 755-66-55") == "+375297556655"
    assert normalize_phone("123") is None
    assert normalize_phone("375111111111") is None


def test_preview_missing_contact_and_delivery(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.houses = [{**HOUSES[0], "pizzeria_active": 0}]
    result = asyncio.run(
        preview_order(
            {
                "items": [{"id": 35, "category": "pizzas", "size": "пинса"}],
                "street": "Независимости просп.",
                "house": "1",
            }
        )
    )
    assert "name" in result["missing"]
    assert "phone" in result["missing"][1]
    assert "доставка может быть недоступна" in result["warning"].lower()


def test_resolve_item_errors(monkeypatch):
    _patch_pzz(monkeypatch)
    empty = asyncio.run(preview_order({"items": []}))
    assert "хотя бы одну" in empty["error"].lower()
    bad_obj = asyncio.run(preview_order({"items": ["x"]}))
    assert "объектом" in bad_obj["error"]
    bad_cat = asyncio.run(preview_order({"items": [{"id": 1, "category": "cars"}]}))
    assert "Неизвестная категория" in bad_cat["error"]
    bad_id = asyncio.run(preview_order({"items": [{"id": "x", "category": "pizzas"}]}))
    assert "числовой id" in bad_id["error"]
    missing = asyncio.run(preview_order({"items": [{"id": 404, "category": "pizzas"}]}))
    assert "не найден" in missing["error"]
    qty = asyncio.run(preview_order({"items": [{"id": 35, "category": "pizzas", "quantity": 99}]}))
    assert "quantity" in qty["error"]
    drink = asyncio.run(
        preview_order({"items": [{"id": 20, "category": "drinks", "quantity": "2"}]})
    )
    assert drink["items"][0]["quantity"] == 2
    aliases = asyncio.run(
        preview_order({"items": [{"id": 35, "category": "pizzas", "size": "36"}]})
    )
    assert aliases["items"][0]["size"] == "big"


def test_quote_payload_without_address():
    payload = quote_payload(
        [{"line_total_byn": 1.5, "title": "x", "raw": {"secret": 1}}],
        contact={"name": "A"},
    )
    assert payload["total_byn"] == 1.5
    assert "raw" not in payload["items"][0]
    assert payload["contact"]["name"] == "A"


def test_place_order_add_item_error(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.add_item_resp = FakeResp({"error": True, "message": "fail"})
    result = asyncio.run(place_order(_order_args(), orders_enabled=True))
    assert "корзину" in result["error"]


def test_place_order_add_item_invalid_json(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.add_item_resp = FakeResp(data=None, status_code=500)
    result = asyncio.run(place_order(_order_args(), orders_enabled=True))
    assert "не принял позицию" in result["error"]


def test_place_order_address_warning(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.update_resp = FakeResp(
        {"error": False, "response": {"data": {"warning": "Зона недоступна"}}}
    )
    result = asyncio.run(place_order(_order_args(), orders_enabled=True))
    assert result["submitted"] is False
    assert "Зона недоступна" in result["error"]


def test_place_order_save_failures(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.save_resp = FakeResp(data=None, status_code=500)
    result = asyncio.run(place_order(_order_args(), orders_enabled=True))
    assert "Нет JSON" in result["error"]
    client.save_resp = _ok({"oops": True})
    result = asyncio.run(place_order(_order_args(), orders_enabled=True))
    assert "не подтвердил" in result["error"]


def test_place_order_online_payment(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.save_resp = _ok(
        {
            "num": 7,
            "sync": "s",
            "total": 405000,
            "payment": "online",
            "bepaid_redirect_url": "https://pay.example/1",
        }
    )
    result = asyncio.run(
        place_order(_order_args(flat="12", comment="без лука"), orders_enabled=True)
    )
    assert result["submitted"] is True
    assert result["payment_url"] == "https://pay.example/1"
    assert "bePaid" in result["message"]
    forms = [data for path, data in client.posts if str(path).endswith("/add-item")]
    assert forms[0]["price"] == 405000
    assert forms[0]["dough"] == "thin"


def test_place_order_drink_uses_price_field(monkeypatch):
    _patch_pzz(monkeypatch)
    args = _order_args()
    args["items"] = [{"id": 20, "category": "drinks", "quantity": 1}]
    result = asyncio.run(place_order(args, orders_enabled=True))
    assert result["submitted"] is True


def test_pzz_tools_disabled(monkeypatch):
    from app.config import get_settings

    _patch_pzz(monkeypatch)
    monkeypatch.setattr(get_settings(), "pzz_enabled", False)
    result = json.loads(asyncio.run(call_tool("pzz_search_menu", '{"query": "кола"}')))
    assert "выключены" in result["error"]


def test_search_menu_via_tool_with_category(monkeypatch):
    _patch_pzz(monkeypatch)
    result = json.loads(
        asyncio.run(call_tool("pzz_search_menu", '{"query": "кола", "category": "drinks"}'))
    )
    assert result["count"] == 1
    assert result["items"][0]["id"] == 20


def _order_args(**extra):
    args = {
        "items": [{"id": 35, "category": "pizzas", "size": "big", "quantity": 1}],
        "name": "Иван",
        "phone": "+375297556655",
        "street": "Независимости просп.",
        "house": "1",
        "confirm": True,
    }
    args.update(extra)
    return args


def test_default_size_fallbacks():
    from app.pzz import _default_size

    assert _default_size({"offers": []}, "big") is None
    offer = {"size": "thin", "label": "Тонкое 36 см", "price_byn": 10}
    assert _default_size({"offers": [offer]}, "тонкое") is offer
    assert _default_size({"offers": [offer]}, "") is offer


def test_quantity_invalid_defaults_to_one(monkeypatch):
    _patch_pzz(monkeypatch)
    result = asyncio.run(
        preview_order({"items": [{"id": 35, "category": "pizzas", "quantity": "nope"}]})
    )
    assert result["items"][0]["quantity"] == 1


def test_snack_medium_without_price():
    item = {**SNACK, "has_medium": 1, "medium_price": 0}
    sizes = [row["size"] for row in summarize_product("snacks", item)["offers"]]
    assert sizes == ["big"]


def test_lookup_house_prefix_suggestions(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.houses = [{"id": 1, "title": "10"}, {"id": 2, "title": "11"}]
    result = asyncio.run(lookup_address("Независимости просп.", "1"))
    assert result["house_suggestions"] == ["10", "11"]


def test_place_order_update_invalid_json_continues(monkeypatch):
    client = _patch_pzz(monkeypatch)
    client.update_resp = FakeResp(data=None, status_code=200)
    result = asyncio.run(place_order(_order_args(), orders_enabled=True))
    assert result["submitted"] is True


def test_lookup_empty_street(monkeypatch):
    _patch_pzz(monkeypatch)
    result = asyncio.run(lookup_address("", "1"))
    assert "Улица не найдена" in result["error"]


def test_preview_returns_address_error(monkeypatch):
    _patch_pzz(monkeypatch)
    result = asyncio.run(
        preview_order(
            {
                "items": [{"id": 35, "category": "pizzas"}],
                "street": "Неттакойулицы",
                "house": "1",
            }
        )
    )
    assert "Улица не найдена" in result["error"]


def test_place_order_returns_preview_errors(monkeypatch):
    _patch_pzz(monkeypatch)
    missing = asyncio.run(
        place_order({"items": [{"id": 35, "category": "pizzas"}]}, orders_enabled=True)
    )
    assert missing.get("submitted") is not True
    assert "Не хватает данных" in missing["error"]
    broken = asyncio.run(place_order({"items": []}, orders_enabled=True))
    assert "хотя бы одну" in broken["error"].lower()


def test_resolve_no_offer(monkeypatch):
    from app import pzz as pzz_mod

    _patch_pzz(monkeypatch)

    def fake_summarize(category, item):
        return {"id": item.get("id"), "title": item.get("title"), "offers": [], "photo": ""}

    monkeypatch.setattr(pzz_mod, "summarize_product", fake_summarize)
    result = asyncio.run(preview_order({"items": [{"id": 35, "category": "pizzas"}]}))
    assert "Нет доступного размера" in result["error"]
