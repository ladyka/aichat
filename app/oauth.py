from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx2 as httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.config import get_settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_SCOPE = "openid email profile"

APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_AUDIENCE = "https://appleid.apple.com"

YANDEX_AUTH_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_USERINFO_URL = "https://login.yandex.ru/info"
YANDEX_SCOPE = "login:info login:email"

VK_AUTH_URL = "https://id.vk.ru/authorize"
VK_TOKEN_URL = "https://id.vk.ru/oauth2/auth"
VK_USERINFO_URL = "https://id.vk.ru/oauth2/user_info"
VK_SCOPE = "email"

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
GITHUB_SCOPE = "read:user user:email"
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "aichat",
    "X-GitHub-Api-Version": "2022-11-28",
}

_JWKS_TTL_SECONDS = 300.0


class OAuthError(Exception):
    """User-facing error in the OAuth flow."""


# In-memory CSRF state store. Fine for a single-process app: the flow lasts seconds.
_pending_states: dict[str, dict[str, str]] = {}
_jwks_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _build_url(base: str, params: dict[str, str]) -> str:
    return f"{base}?{urlencode(params)}"


def create_oauth_state(provider: str, **extra: str) -> str:
    # VK ID requires state of at least 32 chars from [A-Za-z0-9_-].
    state = secrets.token_urlsafe(32)
    _pending_states[state] = {"provider": provider, **extra}
    return state


def consume_oauth_state(state: str | None, provider: str) -> dict[str, str]:
    payload = _pending_states.pop(state, None) if state else None
    if not isinstance(payload, dict) or payload.get("provider") != provider:
        raise OAuthError("Ошибка проверки state (попробуйте ещё раз)")
    return payload


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def google_authorize_url() -> str:
    settings = get_settings()
    return _build_url(
        GOOGLE_AUTH_URL,
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_SCOPE,
            "state": create_oauth_state("google"),
            "access_type": "online",
        },
    )


def apple_authorize_url() -> str:
    settings = get_settings()
    return _build_url(
        APPLE_AUTH_URL,
        {
            "client_id": settings.apple_client_id,
            "redirect_uri": settings.apple_redirect_uri,
            "response_type": "code",
            "scope": "name email",
            "state": create_oauth_state("apple"),
            "response_mode": "form_post",
        },
    )


async def _request_json(
    method: str,
    url: str,
    *,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(method, url, data=data, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        body = ""
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text[:500]
        raise OAuthError(
            f"Не удалось обменять код авторизации{body and f': {body}' or ''}"
        ) from exc


async def _post_form(
    url: str,
    data: dict[str, str],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = await _request_json("POST", url, data=data, headers=headers)
    if not isinstance(payload, dict):
        raise OAuthError("Провайдер вернул неожиданный ответ")
    return payload


async def _get_jwks(url: str) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _jwks_cache.get(url)
    if cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        keys = response.json().get("keys", [])
    _jwks_cache[url] = (now, keys)
    return keys


def _b64_to_int(value: str) -> int:
    return int.from_bytes(base64.urlsafe_b64decode(value + "=="), "big")


def _jwk_to_key(jwk: dict[str, Any]) -> Any:
    if jwk.get("kty") == "RSA":
        numbers = rsa.RSAPublicNumbers(_b64_to_int(jwk["e"]), _b64_to_int(jwk["n"]))
        return numbers.public_key()
    if jwk.get("kty") == "EC":
        curves = {"P-256": ec.SECP256R1, "P-384": ec.SECP384R1}
        curve = curves.get(jwk.get("crv", ""))
        if not curve:
            raise OAuthError("Неподдерживаемая кривая подписи")
        numbers = ec.EllipticCurvePublicNumbers(
            _b64_to_int(jwk["x"]), _b64_to_int(jwk["y"]), curve()
        )
        return numbers.public_key()
    raise OAuthError("Неподдерживаемый тип ключа подписи")


def _signing_key(keys: list[dict[str, Any]], kid: str | None) -> dict[str, Any] | None:
    for key in keys:
        if key.get("kid") == kid:
            return key
    return keys[0] if keys else None


async def _decode_id_token(
    jwks_url: str,
    id_token: str,
    audience: str,
    issuer: str,
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise OAuthError("Некорректный id_token") from exc

    keys = await _get_jwks(jwks_url)
    jwk = _signing_key(keys, header.get("kid"))
    if not jwk:
        raise OAuthError("Не найден ключ подписи провайдера")

    try:
        key = _jwk_to_key(jwk)
        return jwt.decode(
            id_token,
            key,
            algorithms=["RS256", "ES256"],
            audience=audience,
            issuer=issuer,
        )
    except (jwt.PyJWTError, OAuthError) as exc:
        if isinstance(exc, OAuthError):
            raise
        raise OAuthError("Не удалось проверить id_token") from exc


async def exchange_google_code(code: str) -> dict[str, str]:
    settings = get_settings()
    tokens = await _post_form(
        GOOGLE_TOKEN_URL,
        {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
            "code": code,
        },
    )
    id_token = tokens.get("id_token")
    if not id_token:
        raise OAuthError("Google не вернул id_token")
    claims = await _decode_id_token(
        GOOGLE_JWKS_URL, id_token, settings.google_client_id, GOOGLE_ISSUER
    )
    if not claims.get("email"):
        raise OAuthError("Google не вернул email")
    return {
        "email": claims["email"],
        "name": claims.get("name", ""),
        "subject": claims["sub"],
    }


def _apple_client_secret(settings) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.apple_team_id,
        "iat": now,
        "exp": now + 3600,
        "aud": APPLE_AUDIENCE,
        "sub": settings.apple_client_id,
    }
    private_key = load_pem_private_key(settings.apple_private_key.encode(), password=None)
    return jwt.encode(
        claims,
        private_key,
        algorithm="ES256",
        headers={"kid": settings.apple_key_id},
    )


async def exchange_apple_code(code: str) -> dict[str, str]:
    settings = get_settings()
    tokens = await _post_form(
        APPLE_TOKEN_URL,
        {
            "client_id": settings.apple_client_id,
            "client_secret": _apple_client_secret(settings),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.apple_redirect_uri,
        },
    )
    id_token = tokens.get("id_token")
    if not id_token:
        raise OAuthError("Apple не вернул id_token")
    claims = await _decode_id_token(
        APPLE_JWKS_URL, id_token, settings.apple_client_id, APPLE_ISSUER
    )
    return {
        # Apple returns email only on first authorization; the web `user` form field
        # carries the name/email on that first call and we fall back to it in routes.
        "email": claims.get("email") or "",
        "subject": claims["sub"],
    }


def yandex_authorize_url() -> str:
    settings = get_settings()
    return _build_url(
        YANDEX_AUTH_URL,
        {
            "client_id": settings.yandex_client_id,
            "redirect_uri": settings.yandex_redirect_uri,
            "response_type": "code",
            "scope": YANDEX_SCOPE,
            "state": create_oauth_state("yandex"),
        },
    )


def vk_authorize_url() -> str:
    settings = get_settings()
    verifier, challenge = _pkce_pair()
    return _build_url(
        VK_AUTH_URL,
        {
            "response_type": "code",
            "client_id": settings.vk_client_id,
            "redirect_uri": settings.vk_redirect_uri,
            "state": create_oauth_state("vk", code_verifier=verifier),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": VK_SCOPE,
        },
    )


def github_authorize_url() -> str:
    settings = get_settings()
    return _build_url(
        GITHUB_AUTH_URL,
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_redirect_uri,
            "scope": GITHUB_SCOPE,
            "state": create_oauth_state("github"),
        },
    )


def _token_error(tokens: dict[str, Any], provider: str) -> None:
    err = tokens.get("error_description") or tokens.get("error")
    if err:
        raise OAuthError(f"{provider}: {err}")


async def exchange_yandex_code(code: str) -> dict[str, str]:
    settings = get_settings()
    if not code:
        raise OAuthError("Яндекс не вернул код авторизации")
    tokens = await _post_form(
        YANDEX_TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.yandex_client_id,
            "client_secret": settings.yandex_client_secret,
        },
    )
    _token_error(tokens, "Яндекс")
    access_token = tokens.get("access_token")
    if not access_token:
        raise OAuthError("Яндекс не вернул access_token")
    info = await _request_json(
        "GET",
        YANDEX_USERINFO_URL,
        params={"format": "json"},
        headers={"Authorization": f"OAuth {access_token}"},
    )
    if not isinstance(info, dict):
        raise OAuthError("Яндекс вернул неожиданный профиль")
    subject = str(info.get("id") or "")
    email = (info.get("default_email") or "").strip()
    if not email:
        emails = info.get("emails") or []
        if isinstance(emails, list) and emails:
            email = str(emails[0]).strip()
    if not subject:
        raise OAuthError("Яндекс не вернул идентификатор пользователя")
    if not email:
        raise OAuthError("Яндекс не вернул email")
    return {"email": email, "subject": subject, "name": info.get("real_name") or ""}


async def exchange_vk_code(
    code: str,
    *,
    device_id: str,
    code_verifier: str,
    state: str,
) -> dict[str, str]:
    settings = get_settings()
    if not code or not device_id or not code_verifier:
        raise OAuthError("VK не вернул данные для обмена кода")
    tokens = await _post_form(
        VK_TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": settings.vk_client_id,
            "device_id": device_id,
            "redirect_uri": settings.vk_redirect_uri,
            "state": state,
            "service_token": settings.vk_client_secret,
        },
    )
    _token_error(tokens, "VK")
    access_token = tokens.get("access_token")
    if not access_token:
        raise OAuthError("VK не вернул access_token")
    info = await _post_form(
        VK_USERINFO_URL,
        {"client_id": settings.vk_client_id, "access_token": access_token},
    )
    _token_error(info, "VK")
    user = info.get("user") if isinstance(info.get("user"), dict) else info
    subject = str(user.get("user_id") or user.get("id") or "")
    email = (user.get("email") or "").strip()
    if not subject:
        raise OAuthError("VK не вернул идентификатор пользователя")
    if not email:
        raise OAuthError("VK не вернул email — разрешите доступ к почте")
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    return {"email": email, "subject": subject, "name": name}


def _github_pick_email(emails: Any, public_email: str) -> str:
    candidates: list[str] = []
    if isinstance(emails, list):
        ranked = sorted(
            (e for e in emails if isinstance(e, dict) and e.get("email")),
            key=lambda e: (not e.get("primary"), not e.get("verified")),
        )
        for entry in ranked:
            addr = str(entry.get("email") or "").strip()
            if addr.endswith("@users.noreply.github.com"):
                continue
            if addr:
                candidates.append(addr)
        if not candidates:
            for entry in ranked:
                addr = str(entry.get("email") or "").strip()
                if addr:
                    candidates.append(addr)
    public = (public_email or "").strip()
    if public and public not in candidates:
        candidates.append(public)
    if candidates:
        return candidates[0]
    return ""


async def exchange_github_code(code: str) -> dict[str, str]:
    settings = get_settings()
    if not code:
        raise OAuthError("GitHub не вернул код авторизации")
    tokens = await _post_form(
        GITHUB_TOKEN_URL,
        {
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
            "redirect_uri": settings.github_redirect_uri,
        },
        headers={"Accept": "application/json"},
    )
    _token_error(tokens, "GitHub")
    access_token = tokens.get("access_token")
    if not access_token:
        raise OAuthError("GitHub не вернул access_token")
    auth_headers = {**GITHUB_API_HEADERS, "Authorization": f"Bearer {access_token}"}
    info = await _request_json("GET", GITHUB_USER_URL, headers=auth_headers)
    if not isinstance(info, dict):
        raise OAuthError("GitHub вернул неожиданный профиль")
    subject = str(info.get("id") or "")
    if not subject:
        raise OAuthError("GitHub не вернул идентификатор пользователя")
    emails = await _request_json("GET", GITHUB_EMAILS_URL, headers=auth_headers)
    email = _github_pick_email(emails, str(info.get("email") or ""))
    if not email:
        raise OAuthError("GitHub не вернул email — разрешите доступ user:email")
    return {
        "email": email,
        "subject": subject,
        "name": info.get("name") or info.get("login") or "",
    }
