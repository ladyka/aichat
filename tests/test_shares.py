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

    accesses = db.scalars(select(ShareAccess)).all()
    assert len(accesses) == 1
    assert accesses[0].ip

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
    assert client.get("/s/does-not-exist").status_code == 404


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
