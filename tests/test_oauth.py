import asyncio
import base64
import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from sqlalchemy import select

import app.oauth as oauth_mod
from app.config import get_settings
from app.db import OAuthIdentity, User
from tests.conftest import register


def email():
    return f"oauth-{uuid.uuid4().hex[:8]}@example.com"


def _enable_google(settings, monkeypatch, client_id="google-client"):
    monkeypatch.setattr(settings, "public_base_url", "https://example.test")
    monkeypatch.setattr(settings, "google_client_id", client_id)
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    return settings


def _enable_apple(settings, monkeypatch, client_id="apple-client"):
    monkeypatch.setattr(settings, "public_base_url", "https://example.test")
    monkeypatch.setattr(settings, "apple_client_id", client_id)
    monkeypatch.setattr(settings, "apple_team_id", "TEAMID")
    monkeypatch.setattr(settings, "apple_key_id", "KEYID")
    monkeypatch.setattr(
        settings,
        "apple_private_key",
        "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n",
    )
    return settings


def _make_rsa_jwk():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()

    def b64u(integer, size):
        return base64.urlsafe_b64encode(integer.to_bytes(size, "big")).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": "test-rsa",
        "n": b64u(numbers.n, 256),
        "e": b64u(numbers.e, 3),
    }
    return key, jwk


def _make_id_token(claims, key, kid="test-rsa", alg="RS256"):
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def test_oauth_disabled_by_default(client):
    assert client.get("/auth/google", follow_redirects=False).status_code == 303
    assert client.get("/auth/apple", follow_redirects=False).status_code == 303
    page = client.get("/login").text
    assert "Войти через Google" not in page
    assert "Войти через Apple" not in page


def test_oauth_buttons_shown_when_enabled(client, monkeypatch):
    settings = get_settings()
    _enable_google(settings, monkeypatch)
    _enable_apple(settings, monkeypatch)
    page = client.get("/login").text
    assert "Войти через Google" in page
    assert "Войти через Apple" in page


def test_google_start_redirects_to_google(client, monkeypatch):
    settings = get_settings()
    _enable_google(settings, monkeypatch)
    response = client.get("/auth/google", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(oauth_mod.GOOGLE_AUTH_URL)


def test_apple_start_redirects_to_apple(client, monkeypatch):
    settings = get_settings()
    _enable_apple(settings, monkeypatch)
    response = client.get("/auth/apple", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(oauth_mod.APPLE_AUTH_URL)


def test_google_callback_bad_state(client, monkeypatch):
    _enable_google(get_settings(), monkeypatch)
    response = client.get("/auth/google/callback?code=x&state=bogus")
    assert response.status_code == 400


def test_google_callback_creates_user(client, db, monkeypatch):
    _enable_google(get_settings(), monkeypatch)
    state = oauth_mod.create_oauth_state("google")

    async def fake_exchange(code):
        return {"email": email(), "name": "Google User", "subject": "google-sub-1"}

    monkeypatch.setattr("app.routes.oauth.exchange_google_code", fake_exchange)
    response = client.get(f"/auth/google/callback?code=code1&state={state}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/chat")

    user = db.scalar(select(User).where(User.email.like("oauth-%@example.com")))
    assert user is not None
    assert user.password_hash  # unusable but present
    identity = db.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == "google"))
    assert identity is not None
    assert identity.subject == "google-sub-1"
    assert identity.user_id == user.id


def test_google_callback_links_existing_email_user(client, db, monkeypatch):
    _enable_google(get_settings(), monkeypatch)
    address = email()
    register(client, address)
    user = db.scalar(select(User).where(User.email == address))
    assert user is not None

    state = oauth_mod.create_oauth_state("google")

    async def fake_exchange(code):
        return {"email": address, "name": "", "subject": "google-sub-2"}

    monkeypatch.setattr("app.routes.oauth.exchange_google_code", fake_exchange)
    response = client.get(f"/auth/google/callback?code=code1&state={state}", follow_redirects=False)
    assert response.status_code == 303

    identity = db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == "google", OAuthIdentity.subject == "google-sub-2"
        )
    )
    assert identity is not None
    assert identity.user_id == user.id


def test_google_callback_same_subject_returns_same_user(client, db, monkeypatch):
    _enable_google(get_settings(), monkeypatch)
    subject = "google-sub-3"

    async def fake_exchange(code):
        return {"email": email(), "name": "", "subject": subject}

    monkeypatch.setattr("app.routes.oauth.exchange_google_code", fake_exchange)

    for _ in range(2):
        state = oauth_mod.create_oauth_state("google")
        response = client.get(f"/auth/google/callback?code=c&state={state}", follow_redirects=False)
        assert response.status_code == 303

    identities = db.scalars(select(OAuthIdentity).where(OAuthIdentity.subject == subject)).all()
    assert len(identities) == 1


def test_apple_callback_creates_user(client, db, monkeypatch):
    _enable_apple(get_settings(), monkeypatch)
    state = oauth_mod.create_oauth_state("apple")

    async def fake_exchange(code):
        return {"email": email(), "subject": "apple-sub-1"}

    monkeypatch.setattr("app.routes.oauth.exchange_apple_code", fake_exchange)
    response = client.post(
        "/auth/apple/callback",
        data={"code": "code1", "state": state, "user": "{}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/chat")

    identity = db.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == "apple"))
    assert identity is not None
    assert identity.subject == "apple-sub-1"


def test_apple_callback_uses_user_json_email(client, db, monkeypatch):
    _enable_apple(get_settings(), monkeypatch)
    state = oauth_mod.create_oauth_state("apple")
    address = email()

    async def fake_exchange(code):
        return {"email": "", "subject": "apple-sub-2"}

    monkeypatch.setattr("app.routes.oauth.exchange_apple_code", fake_exchange)
    payload = json.dumps({"name": {"firstName": "Ann"}, "email": address})
    response = client.post(
        "/auth/apple/callback",
        data={"code": "c", "state": state, "user": payload},
        follow_redirects=False,
    )
    assert response.status_code == 303
    user = db.scalar(select(User).where(User.email == address))
    assert user is not None


def test_apple_callback_error_param(client, monkeypatch):
    _enable_apple(get_settings(), monkeypatch)
    response = client.post(
        "/auth/apple/callback",
        data={"error": "user_cancelled", "error_description": "Пользователь отменил вход"},
    )
    assert response.status_code == 400
    assert "отменил" in response.text


def test_google_callback_bad_state_after_use(client, monkeypatch):
    _enable_google(get_settings(), monkeypatch)
    state = oauth_mod.create_oauth_state("google")
    oauth_mod.consume_oauth_state(state, "google")
    response = client.get(f"/auth/google/callback?code=x&state={state}")
    assert response.status_code == 400


def test_id_token_verification_rsa(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "aud-client")
    key, jwk = _make_rsa_jwk()
    claims = {
        "iss": oauth_mod.GOOGLE_ISSUER,
        "aud": "aud-client",
        "sub": "rsa-sub",
        "email": email(),
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = _make_id_token(claims, key)

    async def fake_jwks(url):
        return [jwk]

    monkeypatch.setattr(oauth_mod, "_get_jwks", fake_jwks)

    async def fake_post_form(url, data):
        return {"id_token": token}

    monkeypatch.setattr(oauth_mod, "_post_form", fake_post_form)

    profile = asyncio.run(oauth_mod.exchange_google_code("code"))
    assert profile["subject"] == "rsa-sub"
    assert profile["email"].startswith("oauth-")


def test_id_token_rejects_wrong_issuer(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "aud-client")
    key, jwk = _make_rsa_jwk()
    claims = {
        "iss": "https://evil.example",
        "aud": "aud-client",
        "sub": "s",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = _make_id_token(claims, key)

    async def fake_jwks(url):
        return [jwk]

    monkeypatch.setattr(oauth_mod, "_get_jwks", fake_jwks)

    async def fake_post_form(url, data):
        return {"id_token": token}

    monkeypatch.setattr(oauth_mod, "_post_form", fake_post_form)
    with pytest.raises(oauth_mod.OAuthError):
        asyncio.run(oauth_mod.exchange_google_code("code"))


def test_apple_client_secret_is_es256_jwt(monkeypatch):
    settings = get_settings()
    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = ec_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    monkeypatch.setattr(settings, "apple_client_id", "com.example.service")
    monkeypatch.setattr(settings, "apple_team_id", "TEAMID")
    monkeypatch.setattr(settings, "apple_key_id", "KEYID")
    monkeypatch.setattr(settings, "apple_private_key", pem)

    secret = oauth_mod._apple_client_secret(settings)
    header = jwt.get_unverified_header(secret)
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEYID"

    claims = jwt.decode(
        secret,
        ec_key.public_key(),
        algorithms=["ES256"],
        audience=oauth_mod.APPLE_AUDIENCE,
    )
    assert claims["iss"] == "TEAMID"
    assert claims["sub"] == "com.example.service"
