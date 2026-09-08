"""S3-compatible object storage (Cloud.ru Object Storage).

PutObject + public URL only. Bucket must already exist; ACL is not set.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings

logger = logging.getLogger("aichat.storage")

_CACHE_CONTROL = "public, max-age=31536000, immutable"


def object_key(user_id: int, extension: str = "png") -> str:
    now = datetime.now(timezone.utc)
    ext = (extension or "png").lstrip(".").lower() or "png"
    return f"aichat/generated/{user_id}/{now:%Y}/{now:%m}/{now:%d}/{uuid.uuid4().hex}.{ext}"


def save_debug_copy(key: str, body: bytes) -> Path | None:
    """Write the generated object under ``data/`` when ``DEBUG=true`` (local inspect)."""
    settings = get_settings()
    if not settings.debug:
        return None
    rel = Path(key)
    if rel.is_absolute() or ".." in rel.parts:
        logger.warning("generate_image debug skip unsafe key=%s", key)
        return None
    path = settings.root / "data" / rel
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    except Exception:
        logger.exception("generate_image local save failed path=%s", path)
        return None
    logger.info("generate_image local_path=%s bytes=%s", path, len(body))
    return path


def public_object_url(key: str) -> str:
    settings = get_settings()
    if settings.s3_public_base_url:
        return f"{settings.s3_public_base_url}/{key}"
    endpoint = settings.s3_endpoint.rstrip("/")
    if settings.s3_path_style:
        return f"{endpoint}/{settings.s3_bucket}/{key}"
    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{settings.s3_bucket}.{parsed.netloc}/{key}"


def _client() -> Any:
    import boto3
    from botocore.config import Config

    settings = get_settings()
    addressing = "path" if settings.s3_path_style else "virtual"
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_sa_key_secret,
        config=Config(
            s3={"addressing_style": addressing},
            retries={"max_attempts": 2},
            connect_timeout=10,
            read_timeout=15,
        ),
    )


def put_bytes(
    key: str,
    body: bytes,
    content_type: str,
) -> str:
    """Upload bytes and return the public HTTP URL."""
    settings = get_settings()
    if not settings.s3_enabled:
        raise RuntimeError("S3 is not configured")
    extra: dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    try:
        _client().put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=body,
            CacheControl=_CACHE_CONTROL,
            **extra,
        )
    except Exception as exc:
        error_code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            error_code = str((response.get("Error") or {}).get("Code") or "")
        if error_code == "InvalidAccessKeyId" and ":" not in settings.s3_access_key_id:
            logger.warning(
                "s3 put InvalidAccessKeyId: set S3_TENANT_ID "
                "(Cloud.ru access key is assembled as tenant_id:key_id)"
            )
        raise
    url = public_object_url(key)
    logger.info("s3_put bucket=%s key=%s bytes=%s", settings.s3_bucket, key, len(body))
    return url
