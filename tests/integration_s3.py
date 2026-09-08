"""Live PutObject against Cloud.ru using .env. Does not print secrets."""

from __future__ import annotations

import urllib.error
import urllib.request

from app.config import get_settings
from app.storage import _client, object_key, put_bytes

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _anonymous_get(url: str) -> tuple[int, str, bytes]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "aichat-s3-check/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as exc:
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, content_type, b""


def main() -> int:
    settings = get_settings()
    access = settings.s3_access_key_id
    print("endpoint", settings.s3_endpoint or "(empty)")
    print("bucket", settings.s3_bucket or "(empty)")
    print("region", settings.s3_region)
    print("path_style", settings.s3_path_style)
    print("tenant_set", bool(settings.s3_tenant_id))
    print("key_id_set", bool(settings.s3_sa_key_id))
    print("secret_set", bool(settings.s3_sa_key_secret))
    print("access_key_has_colon", ":" in access)
    if not settings.s3_enabled:
        print("FAIL: S3 is not configured")
        return 1

    key = object_key(0, "png")
    client = None
    public_ok = False
    try:
        url = put_bytes(key, _PNG, "image/png")
        client = _client()
        body = client.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
        if body != _PNG:
            print("FAIL: authenticated get does not match upload")
            return 1
        print("put+get ok", key)
        status, content_type, downloaded = _anonymous_get(url)
        print("public_url", url)
        print("anonymous_get", status, content_type, f"{len(downloaded)} bytes")
        if status == 200 and downloaded == _PNG:
            public_ok = True
        else:
            print("FAIL: public download did not return the uploaded file")
            return 1
    except Exception as exc:
        error = getattr(exc, "response", None)
        code = ""
        message = ""
        if isinstance(error, dict):
            err = error.get("Error") or {}
            code = str(err.get("Code") or "")
            message = str(err.get("Message") or "")
        print("FAIL:", code or type(exc).__name__, message or str(exc).split(":")[0])
        return 1
    finally:
        try:
            (client or _client()).delete_object(Bucket=settings.s3_bucket, Key=key)
        except Exception as exc:
            err = getattr(exc, "response", None)
            code = ""
            if isinstance(err, dict):
                code = str((err.get("Error") or {}).get("Code") or "")
            print("WARN: delete", code or type(exc).__name__, key)

    if public_ok:
        print("SUCCESS: public download works")
        return 0
    print("FAIL: public download")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
