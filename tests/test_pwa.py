from __future__ import annotations

import json
import uuid

from tests.conftest import register


def _email() -> str:
    return f"pwa-{uuid.uuid4().hex[:8]}@example.com"


def test_service_worker_is_served_from_the_root(client, monkeypatch, tmp_path):
    """`/sw.js` отдаётся из корня: только так у service worker'а scope `/`."""
    from app.config import get_settings

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "sw.js").write_text("self.addEventListener('install', () => {});\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "root", tmp_path)

    response = client.get("/sw.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert "addEventListener" in response.text


def test_service_worker_without_build_returns_404(client, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "root", tmp_path)

    assert client.get("/sw.js").status_code == 404


def test_web_manifest_is_served_with_manifest_content_type(client, monkeypatch, tmp_path):
    from app.config import get_settings

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "manifest.webmanifest").write_text('{"start_url": "/chat"}', encoding="utf-8")
    monkeypatch.setattr(get_settings(), "root", tmp_path)

    response = client.get("/manifest.webmanifest")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/manifest+json"
    assert response.json()["start_url"] == "/chat"


def test_web_manifest_without_build_returns_404(client, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "root", tmp_path)

    assert client.get("/manifest.webmanifest").status_code == 404


def test_chat_page_declares_pwa(client):
    """PWA-теги — только у страницы чата, остальной сайт не устанавливаемый."""
    register(client, _email())

    response = client.get("/chat")

    assert response.status_code == 200
    assert '<link rel="manifest" href="/manifest.webmanifest">' in response.text
    assert '<meta name="theme-color" content="#0f7a5f">' in response.text
    assert '<link rel="apple-touch-icon" href="/chat-ui/apple-touch-icon.png">' in response.text


def test_landing_page_is_not_a_pwa(client):
    response = client.get("/")

    assert '<link rel="manifest"' not in response.text
    assert "apple-mobile-web-app-capable" not in response.text


def test_pwa_manifest_matches_the_chat_entry():
    """`start_url` — `/chat`, а не `/chat-ui/`.

    Инвариант неочевидный: service worker лежит в корне и контролирует `/chat`,
    а `/chat-ui/` — только хранилище ассетов. Если поменять `start_url`, установка
    сломается (навигация окажется вне зоны контроля SW), поэтому фиксируем тестом.
    """
    from app.config import get_settings

    public = get_settings().root / "frontend" / "public"
    manifest = json.loads((public / "manifest.webmanifest").read_text(encoding="utf-8"))

    assert manifest["start_url"] == "/chat"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert {"192x192", "512x512"} <= {icon["sizes"] for icon in manifest["icons"]}
    assert any(icon["purpose"] == "maskable" for icon in manifest["icons"])
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/chat-ui/")
        assert (public / icon["src"].removeprefix("/chat-ui/")).is_file()
