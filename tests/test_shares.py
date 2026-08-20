import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import ShareAccess, ShareLink
from tests.conftest import register


def email():
    return f"share-{uuid.uuid4().hex[:8]}@example.com"


def _auth(client):
    register(client, email())


def _create_conversation(client):
    return client.post("/api/conversations", json={})


def _seed_conversation(client):
    conv = _create_conversation(client).json()
    client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={
            "messages": [
                {"role": "user", "content": "Первый вопрос"},
                {"role": "assistant", "content": "Ответ от чатбота"},
            ]
        },
    )
    return conv


def test_share_requires_auth(client):
    assert client.get("/api/conversations/1/share").status_code == 401
    assert client.post("/api/conversations/1/share").status_code == 401
    assert client.post("/api/conversations/1/share/revoke").status_code == 401


def test_share_lifecycle(client, db):
    _auth(client)
    conv = _seed_conversation(client)

    created = client.post(f"/api/conversations/{conv['id']}/share")
    assert created.status_code == 200
    data = created.json()
    assert data["shared"] is True
    assert data["key"].startswith("s_")
    assert data["expires_at"] is not None
    key = data["key"]

    existing = client.get(f"/api/conversations/{conv['id']}/share")
    assert existing.status_code == 200
    body = existing.json()
    assert body["shared"] is True
    assert "key" not in body  # ключ хранится только в виде хэша

    # anonymous visitor can open the page
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        page = anon.get(f"/s/{key}")
        assert page.status_code == 200
        assert "Первый вопрос" in page.text
        assert "Ответ от чатбота" in page.text
        assert "Чатбот" in page.text
        assert "Просмотр" in page.text
        assert email() not in page.text  # автор не показывается
        assert 'property="og:title"' in page.text
        assert "og:description" in page.text
        assert "Первый вопрос" in page.text
        assert 'property="og:image"' in page.text
        assert "/static/og/share.png" in page.text
        assert 'property="og:type" content="article"' in page.text
        assert f'property="og:url" content="http://testserver/s/{key}"' in page.text
        assert "twitter:card" in page.text

    accesses = db.scalars(select(ShareAccess)).all()
    assert len(accesses) == 1
    assert accesses[0].ip
    assert accesses[0].visitor_kind == "human"
    assert accesses[0].visitor_label == "browser"

    # owner can revoke
    revoked = client.post(f"/api/conversations/{conv['id']}/share/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True

    assert client.get(f"/s/{key}").status_code == 404
    assert client.get(f"/api/conversations/{conv['id']}/share").status_code == 404

    # creating again after revoke makes a new link
    new_key = client.post(f"/api/conversations/{conv['id']}/share").json()["key"]
    assert new_key != key


def test_share_not_found(client):
    page = client.get("/s/does-not-exist")
    assert page.status_code == 404
    assert "Первый вопрос" not in page.text
    assert 'content="noindex"' in page.text
    assert "отозвана, истекла или не существует" in page.text


def test_share_access_classifies_crawlers_and_bots(client, db):
    _auth(client)
    conv = _seed_conversation(client)
    key = client.post(f"/api/conversations/{conv['id']}/share").json()["key"]

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        assert anon.get(f"/s/{key}", headers={"User-Agent": "TelegramBot"}).status_code == 200
        assert anon.get(f"/s/{key}", headers={"User-Agent": "curl/8.5.0"}).status_code == 200
        assert (
            anon.get(
                f"/s/{key}",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                    )
                },
            ).status_code
            == 200
        )

    kinds = {(row.visitor_kind, row.visitor_label) for row in db.scalars(select(ShareAccess)).all()}
    assert ("crawler", "telegram") in kinds
    assert ("bot", "curl") in kinds
    assert ("human", "browser") in kinds


def test_share_expired(client, db):
    _auth(client)
    conv = _seed_conversation(client)
    key = client.post(f"/api/conversations/{conv['id']}/share").json()["key"]

    share = db.scalar(select(ShareLink).where(ShareLink.conversation_id == conv["id"]))
    share.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    assert client.get(f"/s/{key}").status_code == 404


def test_share_is_owned(client):
    _auth(client)
    conv = _seed_conversation(client)

    register(client, email())
    assert client.get(f"/api/conversations/{conv['id']}/share").status_code == 404
    assert client.post(f"/api/conversations/{conv['id']}/share").status_code == 404
    assert client.post(f"/api/conversations/{conv['id']}/share/revoke").status_code == 404
