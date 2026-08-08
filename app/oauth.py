from __future__ import annotations

import base64
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

_JWKS_TTL_SECONDS = 300.0


class OAuthError(Exception):
    """User-facing error in the OAuth flow."""


# In-memory CSRF state store. Fine for a single-process app: the flow lasts seconds.
_pending_states: dict[str, dict[str, str]] = {}
_jwks_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _build_url(base: str, params: dict[str, str]) -> str:
    return f"{base}?{urlencode(params)}"


def create_oauth_state(provider: str) -> str:
    state = secrets.token_urlsafe(24)
    _pending_states[state] = {"provider": provider}
    return state


def consume_oauth_state(state: str | None, provider: str) -> None:
    if not state or _pending_states.pop(state, None) != {"provider": provider}:
        raise OAuthError("Ошибка проверки state (попробуйте ещё раз)")


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


async def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, data=data)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        body = ""
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text[:500]
        raise OAuthError(
            f"Не удалось обменять код авторизации{body and f': {body}' or ''}"
        ) from exc


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
