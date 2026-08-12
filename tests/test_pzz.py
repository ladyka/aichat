import asyncio
import json

from app.pzz import clear_caches, price_byn, summarize_product
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


class FakePzzClient:
    def __init__(self, *a, **kw):
        self.posts = []
        self.gets = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        return None

    def _path(self, path):
        return str(path)

    async def get(self, path, **kw):
        path = self._path(path)
        self.gets.append(path)
        if path in ("/", "") or path.endswith("://pzz.by/"):
            return FakeResp(data=None)
        if path.endswith("/pizzas") or path.endswith("/pizzas/"):
            return _ok([PIZZA])
        if "/streets/" in path:
            return _ok(HOUSES)
        if path.endswith("/streets") or path.endswith("/streets/"):
            return _ok(STREETS)
        if any(
            path.endswith(f"/{cat}")
            for cat in ("snacks", "desserts", "drinks", "sauces", "warmers")
        ):
            return _ok([])
        return FakeResp({"error": True, "code": 404}, status_code=404)

    async def post(self, path, **kw):
        path = self._path(path)
        data = kw.get("data") or {}
        self.posts.append((path, data))
        if path.endswith("/basket/save"):
            return _ok({"num": 42, "sync": "abc", "total": 405000, "payment": "cash"})
        if path.endswith("/basket/update-address"):
            return _ok({})
        if path.endswith("/basket/add-item") or path.endswith("/basket/house"):
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
