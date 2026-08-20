"""S3-compatible object storage (Cloud.ru Object Storage).

PutObject + public URL only. Bucket must already exist; ACL is not set.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings

logger = logging.getLogger("aichat.storage")

_CACHE_CONTROL = "public, max-age=31536000, immutable"


def object_key(user_id: int, extension: str = "png") -> str:
    now = datetime.now(timezone.utc)
    ext = (extension or "png").lstrip(".").lower() or "png"
    return f"aichat/generated/{user_id}/{now:%Y}/{now:%m}/{now:%d}/{uuid.uuid4().hex}.{ext}"


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
    addressing = "path" if settings.s3_path_style else "auto"
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.sa_key_id,
        aws_secret_access_key=settings.sa_key_secret,
        config=Config(s3={"addressing_style": addressing}),
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
    _client().put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=body,
        ContentType=content_type or "application/octet-stream",
        ContentLength=len(body),
        CacheControl=_CACHE_CONTROL,
    )
    url = public_object_url(key)
    logger.info("s3_put bucket=%s key=%s bytes=%s", settings.s3_bucket, key, len(body))
    return url
