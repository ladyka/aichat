import uuid

from app.skills import MAX_SKILL_BODY, SKILL_TOO_LARGE
from tests.conftest import register


def email():
    return f"skill-ui-{uuid.uuid4().hex[:8]}@example.com"


def test_catalog_is_public(client):
    page = client.get("/catalog")
    assert page.status_code == 200
    assert "Каталог навыков" in page.text
    assert client.get("/skills", follow_redirects=False).status_code == 303


def test_skills_library_and_editor(client):
    register(client, email())
    lib = client.get("/skills")
    assert lib.status_code == 200
    assert "Пока нет навыков" in lib.text

    created = client.post(
        "/skills/new",
        data={
            "title": "Налоги",
            "description": "НК РБ",
            "body": "Ссылайся на кодекс.",
            "public": "on",
            "default": "on",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]
    assert location.startswith("/skills/")
    editor = client.get(location)
    assert editor.status_code == 200
    assert "Налоги" in editor.text
    assert "Показывать в каталоге" in editor.text
    assert "/catalog/skills/" in editor.text

    catalog = client.get("/catalog")
    assert "Налоги" in catalog.text
    skill_id = location.rsplit("/", 1)[-1]
    card = client.get(f"/catalog/skills/{skill_id}")
    assert card.status_code == 200
    assert "Ссылайся на кодекс" in card.text

    settings = client.get("/settings")
    assert "Навыки в новых чатах" in settings.text
    assert "Налоги" in settings.text


def test_private_skill_not_in_catalog_html(client):
    register(client, email())
    created = client.post(
        "/skills/new",
        data={"title": "Черновик", "body": "secret", "description": ""},
        follow_redirects=False,
    )
    skill_id = created.headers["location"].rsplit("/", 1)[-1]
    assert client.get("/catalog").status_code == 200
    assert "Черновик" not in client.get("/catalog").text
    missing = client.get(f"/catalog/skills/{skill_id}")
    assert missing.status_code == 404


def test_catalog_copy_requires_login_then_returns(client):
    owner = email()
    copier = email()
    register(client, owner)
    created = client.post(
        "/skills/new",
        data={"title": "Публичный", "body": "текст", "public": "on"},
        follow_redirects=False,
    )
    skill_id = created.headers["location"].rsplit("/", 1)[-1]
    client.get("/logout")

    copy = client.post(
        f"/catalog/skills/{skill_id}/copy",
        follow_redirects=False,
    )
    assert copy.status_code == 303
    assert copy.headers["location"].startswith("/login")
    assert f"/catalog/skills/{skill_id}" in copy.headers["location"]

    register(client, copier)
    copied = client.post(
        f"/catalog/skills/{skill_id}/copy",
        follow_redirects=False,
    )
    assert copied.status_code == 303
    assert copied.headers["location"].startswith("/skills/")
    page = client.get(copied.headers["location"])
    assert "Скопировано из каталога" in page.text
    assert (
        client.get(f"/catalog/skills/{copied.headers['location'].rsplit('/', 1)[-1]}").status_code
        == 404
    )


def test_login_next_returns_to_catalog(client):
    address = email()
    register(client, address)
    client.get("/logout")
    page = client.get("/login?next=/catalog/skills/1")
    assert 'name="next"' in page.text
    assert 'value="/catalog/skills/1"' in page.text
    response = client.post(
        "/login",
        data={"email": address, "password": "secret123", "next": "/catalog"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/catalog")


def test_login_rejects_external_next(client):
    address = email()
    register(client, address)
    client.get("/logout")
    response = client.post(
        "/login",
        data={
            "email": address,
            "password": "secret123",
            "next": "https://evil.example/phish",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/chat")


def test_delete_skill_from_editor(client):
    register(client, email())
    created = client.post(
        "/skills/new",
        data={"title": "Удалить", "body": "x"},
        follow_redirects=False,
    )
    skill_id = created.headers["location"].rsplit("/", 1)[-1]
    deleted = client.post(f"/skills/{skill_id}/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert deleted.headers["location"].endswith("/skills")
    assert client.get(f"/skills/{skill_id}").status_code == 404


def test_foreign_skill_editor_is_404(client):
    register(client, email())
    created = client.post(
        "/skills/new",
        data={"title": "Чужой", "body": "x"},
        follow_redirects=False,
    )
    skill_id = created.headers["location"].rsplit("/", 1)[-1]
    register(client, email())
    assert client.get(f"/skills/{skill_id}").status_code == 404
    assert client.post(f"/skills/{skill_id}", data={"title": "hack"}).status_code == 404


def test_new_skill_form_and_edit_unpublish(client):
    register(client, email())
    form = client.get("/skills/new")
    assert form.status_code == 200
    assert "Новый навык" in form.text

    created = client.post(
        "/skills/new",
        data={
            "title": "Публичный",
            "description": "desc",
            "body": "инструкция",
            "public": "on",
        },
        follow_redirects=False,
    )
    skill_id = created.headers["location"].rsplit("/", 1)[-1]
    assert client.get(f"/catalog/skills/{skill_id}").status_code == 200

    saved = client.post(
        f"/skills/{skill_id}",
        data={
            "title": "Уже личный",
            "description": "desc",
            "body": "инструкция 2",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert "saved=1" in saved.headers["location"]
    editor = client.get(saved.headers["location"])
    assert "Уже личный" in editor.text
    assert "Сохранено" in editor.text
    assert client.get(f"/catalog/skills/{skill_id}").status_code == 404


def test_settings_default_skills_form(client, mock_models):
    register(client, email())
    created = client.post(
        "/skills/new",
        data={"title": "Дефолт", "body": "x", "default": "on"},
        follow_redirects=False,
    )
    skill_id = created.headers["location"].rsplit("/", 1)[-1]
    page = client.get("/settings")
    assert 'name="default_skill_ids"' in page.text
    assert skill_id in page.text

    cleared = client.post(
        "/settings",
        data={"preferred_model": "default"},
        follow_redirects=False,
    )
    assert cleared.status_code == 200
    assert client.get("/api/settings").json()["default_skill_ids"] == []

    enabled = client.post(
        "/settings",
        data={"preferred_model": "default", "default_skill_ids": skill_id},
    )
    assert enabled.status_code == 200
    assert client.get("/api/settings").json()["default_skill_ids"] == [skill_id]


def test_skill_form_rejects_too_large_body(client):
    register(client, email())
    body = "x" * (MAX_SKILL_BODY + 1)
    created = client.post(
        "/skills/new",
        data={"title": "Огромный", "body": body},
    )
    assert created.status_code == 413
    assert SKILL_TOO_LARGE in created.text


def test_catalog_copy_unpublished_is_404(client):
    register(client, email())
    created = client.post(
        "/skills/new",
        data={"title": "Черновик", "body": "secret"},
        follow_redirects=False,
    )
    skill_id = created.headers["location"].rsplit("/", 1)[-1]
    register(client, email())
    missing = client.post(f"/catalog/skills/{skill_id}/copy")
    assert missing.status_code == 404
