import uuid

import pytest
from fastapi import HTTPException

from tests.conftest import create_token, login, register


def email():
    return f"page-{uuid.uuid4().hex[:8]}@example.com"


def test_landing(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "aichat" in response.text


def test_login_page(client):
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200


def test_register_validation(client):
    address = email()
    assert register(client, "not-an-email").status_code == 400
    assert register(client, address, password="123").status_code == 400
    response = client.post(
        "/register",
        data={"email": address, "password": "secret123", "password2": "other123"},
    )
    assert response.status_code == 400
    register(client, address)
    assert register(client, address).status_code == 400


def test_register_success_redirects_to_chat(client):
    response = register(client, email())
    assert response.status_code == 303
    assert response.headers["location"].endswith("/chat")


def test_login_wrong_password(client):
    address = email()
    register(client, address)
    response = login(client, address, password="wrongpass")
    assert response.status_code == 400
    assert "Неверный email или пароль" in response.text


def test_login_success(client):
    address = email()
    register(client, address)
    response = login(client, address)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/chat")


def test_authenticated_user_is_redirected_away_from_auth(client):
    register(client, email())
    assert client.get("/login", follow_redirects=False).status_code == 303
    assert client.get("/register", follow_redirects=False).status_code == 303


def test_chat_page_requires_auth(client):
    assert client.get("/chat", follow_redirects=False).status_code == 303
    register(client, email())
    assert client.get("/chat").status_code == 200


def test_logout(client):
    register(client, email())
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/chat", follow_redirects=False).status_code == 303


def test_settings_page(client, mock_models):
    assert client.get("/settings", follow_redirects=False).status_code == 303
    register(client, email())
    response = client.get("/settings")
    assert response.status_code == 200
    assert "Модель" in response.text


def test_settings_save_model(client, mock_models):
    register(client, email())
    response = client.post("/settings", data={"preferred_model": "default"})
    assert response.status_code == 200
    assert "Сохранено" in response.text


def test_settings_invalid_model(client, mock_models, monkeypatch):
    from app.routes import pages as pages_mod

    def raise_bad_model(model):
        raise HTTPException(status_code=400, detail="Model 'nope' is not available")

    monkeypatch.setattr(pages_mod, "resolve_upstream_model", raise_bad_model)
    register(client, email())
    response = client.post("/settings", data={"preferred_model": "nope"})
    assert response.status_code == 400
    assert "not available" in response.text


def test_tokens_page_requires_auth(client):
    assert client.get("/tokens", follow_redirects=False).status_code == 303


def test_create_token_shows_value_and_usage(client):
    register(client, email())
    token, response = create_token(client)
    assert token.startswith("aichat_")
    assert "Скопируйте токен сейчас" in response.text
    assert "сегодня: 0/10" in response.text


def test_token_creation_limit(client):
    register(client, email())
    for i in range(10):
        token, response = create_token(client, name=f"t{i}")
        assert token is not None
    token, response = create_token(client, name="t11")
    assert token is None
    assert "не более 10 токенов" in response.text


def test_revoke_token(client, db):
    from app.auth import hash_token
    from sqlalchemy import select

    from app.db import ApiToken

    register(client, email())
    token, response = create_token(client)
    assert token is not None

    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    assert row is not None

    response = client.post(f"/tokens/{row.id}/revoke", follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/tokens")
    assert "Активных токенов пока нет" in page.text
