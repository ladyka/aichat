from app.storage import object_key, public_object_url


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
