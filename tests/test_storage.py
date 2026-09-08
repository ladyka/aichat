from app.storage import _client, object_key, public_object_url, put_bytes, save_debug_copy


def test_object_key_layout():
    key = object_key(42, "png")
    parts = key.split("/")
    assert parts[0] == "aichat"
    assert parts[1] == "generated"
    assert parts[2] == "42"
    assert len(parts[3]) == 4
    assert len(parts[4]) == 2
    assert len(parts[5]) == 2
    assert parts[6].endswith(".png")
    assert len(parts[6]) == 32 + 4


def test_public_url_prefers_base(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "s3_public_base_url", "https://bucket.s3.cloud.ru")
    monkeypatch.setattr(settings, "s3_endpoint", "https://s3.cloud.ru")
    monkeypatch.setattr(settings, "s3_bucket", "ignored")
    monkeypatch.setattr(settings, "s3_path_style", True)
    assert public_object_url("a/b.png") == "https://bucket.s3.cloud.ru/a/b.png"


def test_public_url_path_style_without_base(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "s3_public_base_url", "")
    monkeypatch.setattr(settings, "s3_endpoint", "https://s3.cloud.ru")
    monkeypatch.setattr(settings, "s3_bucket", "aichat")
    monkeypatch.setattr(settings, "s3_path_style", True)
    assert public_object_url("k.png") == "https://s3.cloud.ru/aichat/k.png"


def test_s3_client_matches_cloud_ru_script(monkeypatch):
    import boto3

    from app.config import get_settings

    captured = {}

    def fake_client(service_name, **kwargs):
        captured["service"] = service_name
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", fake_client)
    settings = get_settings()
    monkeypatch.setattr(settings, "s3_endpoint", "https://s3.cloud.ru")
    monkeypatch.setattr(settings, "s3_region", "ru-central-1")
    monkeypatch.setattr(settings, "s3_path_style", True)
    monkeypatch.setattr(settings, "s3_tenant_id", "tenant")
    monkeypatch.setattr(settings, "s3_sa_key_id", "key")
    monkeypatch.setattr(settings, "s3_sa_key_secret", "secret")
    _client()
    assert captured["aws_access_key_id"] == "tenant:key"
    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == "https://s3.cloud.ru"
    assert captured["region_name"] == "ru-central-1"
    cfg = captured["config"]
    assert cfg.s3["addressing_style"] == "path"
    assert cfg.s3.get("payload_signing_enabled") is None


def test_put_bytes_hints_cloud_ru_key_format(monkeypatch, caplog):
    import logging

    from botocore.exceptions import ClientError

    from app.config import get_settings

    class FakeClient:
        def put_object(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "InvalidAccessKeyId", "Message": "unknown"}},
                "PutObject",
            )

    settings = get_settings()
    monkeypatch.setattr(settings, "s3_endpoint", "https://s3.cloud.ru")
    monkeypatch.setattr(settings, "s3_bucket", "aichat")
    monkeypatch.setattr(settings, "s3_sa_key_id", "only-uuid")
    monkeypatch.setattr(settings, "s3_tenant_id", "")
    monkeypatch.setattr(settings, "s3_sa_key_secret", "secret")
    monkeypatch.setattr("app.storage._client", lambda: FakeClient())
    with caplog.at_level(logging.WARNING, logger="aichat.storage"):
        try:
            put_bytes("a.png", b"x", "image/png")
        except ClientError:
            pass
        else:
            raise AssertionError("expected ClientError")
    assert "S3_TENANT_ID" in caplog.text


def test_save_debug_copy_writes_under_data(monkeypatch, tmp_path):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "root", tmp_path)
    key = "aichat/generated/1/2026/08/21/abc.png"
    path = save_debug_copy(key, b"png-bytes")
    assert path == tmp_path / "data" / key
    assert path.read_bytes() == b"png-bytes"


def test_save_debug_copy_skips_when_debug_off(monkeypatch, tmp_path):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "root", tmp_path)
    assert save_debug_copy("aichat/generated/1/x.png", b"x") is None
    assert not (tmp_path / "data").exists()
