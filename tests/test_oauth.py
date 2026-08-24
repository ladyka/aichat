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


def _enable_yandex(settings, monkeypatch, client_id="yandex-client"):
    monkeypatch.setattr(settings, "public_base_url", "https://example.test")
    monkeypatch.setattr(settings, "yandex_client_id", client_id)
    monkeypatch.setattr(settings, "yandex_client_secret", "yandex-secret")
    return settings


def _enable_vk(settings, monkeypatch, client_id="vk-client"):
    monkeypatch.setattr(settings, "public_base_url", "https://example.test")
    monkeypatch.setattr(settings, "vk_client_id", client_id)
    monkeypatch.setattr(settings, "vk_client_secret", "vk-service-token")
    return settings


def _enable_github(settings, monkeypatch, client_id="github-client"):
    monkeypatch.setattr(settings, "public_base_url", "https://example.test")
    monkeypatch.setattr(settings, "github_client_id", client_id)
    monkeypatch.setattr(settings, "github_client_secret", "github-secret")
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
    for path in (
        "/auth/google",
        "/auth/apple",
        "/auth/yandex",
        "/auth/vk",
        "/auth/github",
    ):
        assert client.get(path, follow_redirects=False).status_code == 303
    page = client.get("/login").text
    assert "Войти через Google" not in page
    assert "Войти через Apple" not in page
    assert "Войти через Яндекс" not in page
    assert "Войти через VK" not in page
    assert "Войти через GitHub" not in page


def test_oauth_buttons_shown_when_enabled(client, monkeypatch):
    settings = get_settings()
    _enable_google(settings, monkeypatch)
    _enable_apple(settings, monkeypatch)
    _enable_yandex(settings, monkeypatch)
    _enable_vk(settings, monkeypatch)
    _enable_github(settings, monkeypatch)
    page = client.get("/login").text
    assert "Войти через Google" in page
    assert "Войти через Apple" in page
    assert "Войти через Яндекс" in page
    assert "Войти через VK" in page
    assert "Войти через GitHub" in page
    register_page = client.get("/register").text
    assert "Войти через Яндекс" in register_page


def test_oauth_button_only_for_configured_provider(client, monkeypatch):
    _enable_yandex(get_settings(), monkeypatch)
    page = client.get("/login").text
    assert "Войти через Яндекс" in page
    assert "Войти через Google" not in page
    assert "Войти через GitHub" not in page


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


def test_yandex_start_redirects(client, monkeypatch):
    _enable_yandex(get_settings(), monkeypatch)
    response = client.get("/auth/yandex", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(oauth_mod.YANDEX_AUTH_URL)


def test_vk_start_redirects_with_pkce(client, monkeypatch):
    _enable_vk(get_settings(), monkeypatch)
    response = client.get("/auth/vk", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(oauth_mod.VK_AUTH_URL)
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location


def test_github_start_redirects(client, monkeypatch):
    _enable_github(get_settings(), monkeypatch)
    response = client.get("/auth/github", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(oauth_mod.GITHUB_AUTH_URL)


def test_yandex_callback_creates_user(client, db, monkeypatch):
    _enable_yandex(get_settings(), monkeypatch)
    state = oauth_mod.create_oauth_state("yandex")

    async def fake_exchange(code):
        return {"email": email(), "subject": "yandex-sub-1", "name": "Ya"}

    monkeypatch.setattr("app.routes.oauth.exchange_yandex_code", fake_exchange)
    response = client.get(f"/auth/yandex/callback?code=code1&state={state}", follow_redirects=False)
    assert response.status_code == 303
    identity = db.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == "yandex"))
    assert identity is not None
    assert identity.subject == "yandex-sub-1"


def test_vk_callback_creates_user(client, db, monkeypatch):
    _enable_vk(get_settings(), monkeypatch)
    state = oauth_mod.create_oauth_state("vk", code_verifier="verifier-value")

    async def fake_exchange(code, *, device_id, code_verifier, state):
        assert device_id == "dev-1"
        assert code_verifier == "verifier-value"
        return {"email": email(), "subject": "vk-sub-1", "name": "Vk User"}

    monkeypatch.setattr("app.routes.oauth.exchange_vk_code", fake_exchange)
    response = client.get(
        f"/auth/vk/callback?code=code1&state={state}&device_id=dev-1",
        follow_redirects=False,
    )
    assert response.status_code == 303
    identity = db.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == "vk"))
    assert identity is not None
    assert identity.subject == "vk-sub-1"


def test_github_callback_creates_user(client, db, monkeypatch):
    _enable_github(get_settings(), monkeypatch)
    state = oauth_mod.create_oauth_state("github")

    async def fake_exchange(code):
        return {"email": email(), "subject": "42", "name": "gh"}

    monkeypatch.setattr("app.routes.oauth.exchange_github_code", fake_exchange)
    response = client.get(f"/auth/github/callback?code=code1&state={state}", follow_redirects=False)
    assert response.status_code == 303
    identity = db.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == "github"))
    assert identity is not None
    assert identity.subject == "42"


def test_github_callback_error_param(client, monkeypatch):
    _enable_github(get_settings(), monkeypatch)
    response = client.get("/auth/github/callback?error=access_denied")
    assert response.status_code == 400


def test_exchange_yandex_code(monkeypatch):
    _enable_yandex(get_settings(), monkeypatch)

    async def fake_post(url, data, headers=None):
        assert url == oauth_mod.YANDEX_TOKEN_URL
        return {"access_token": "ya-token"}

    async def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url == oauth_mod.YANDEX_USERINFO_URL
        return {"id": "1001", "default_email": email(), "real_name": "Ivan"}

    monkeypatch.setattr(oauth_mod, "_post_form", fake_post)
    monkeypatch.setattr(oauth_mod, "_request_json", fake_request)
    profile = asyncio.run(oauth_mod.exchange_yandex_code("code"))
    assert profile["subject"] == "1001"
    assert "@" in profile["email"]


def test_exchange_vk_code(monkeypatch):
    _enable_vk(get_settings(), monkeypatch)

    async def fake_post(url, data, headers=None):
        if url == oauth_mod.VK_TOKEN_URL:
            assert data["code_verifier"] == "ver"
            assert data["service_token"] == "vk-service-token"
            return {"access_token": "vk-token"}
        assert url == oauth_mod.VK_USERINFO_URL
        return {
            "user": {
                "user_id": "777",
                "email": "vk-user@example.com",
                "first_name": "A",
                "last_name": "B",
            }
        }

    monkeypatch.setattr(oauth_mod, "_post_form", fake_post)
    profile = asyncio.run(
        oauth_mod.exchange_vk_code("code", device_id="dev", code_verifier="ver", state="st")
    )
    assert profile["subject"] == "777"
    assert profile["email"] == "vk-user@example.com"


def test_exchange_github_prefers_primary_email(monkeypatch):
    _enable_github(get_settings(), monkeypatch)

    async def fake_post(url, data, headers=None):
        assert headers["Accept"] == "application/json"
        return {"access_token": "gh-token"}

    async def fake_request(method, url, **kwargs):
        if url == oauth_mod.GITHUB_USER_URL:
            return {"id": 99, "login": "octo", "email": None}
        return [
            {"email": "hidden@users.noreply.github.com", "primary": False, "verified": True},
            {"email": "real@example.com", "primary": True, "verified": True},
        ]

    monkeypatch.setattr(oauth_mod, "_post_form", fake_post)
    monkeypatch.setattr(oauth_mod, "_request_json", fake_request)
    profile = asyncio.run(oauth_mod.exchange_github_code("code"))
    assert profile["subject"] == "99"
    assert profile["email"] == "real@example.com"


def test_github_pick_email_skips_noreply():
    emails = [
        {"email": "me@users.noreply.github.com", "primary": True, "verified": True},
        {"email": "me@example.com", "primary": False, "verified": True},
    ]
    assert oauth_mod._github_pick_email(emails, "") == "me@example.com"
