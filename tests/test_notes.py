import uuid

from sqlalchemy import select

from app.db import ConversationNote, Note
from app.notes import MAX_NOTE_BODY, download_filename
from tests.conftest import register


def email():
    return f"note-{uuid.uuid4().hex[:8]}@example.com"


def _auth(client, addr=None):
    register(client, addr or email())


def _create_conversation(client, title="Чат"):
    return client.post("/api/conversations", json={"title": title}).json()


def test_note_requires_auth(client):
    assert client.get("/api/conversations/1/note").status_code == 401
    assert client.put("/api/conversations/1/note", json={"body": "x"}).status_code == 401
    assert client.get("/api/conversations/1/note/download").status_code == 401


def test_note_crud_and_download(client):
    _auth(client)
    conv = _create_conversation(client)
    path = f"/api/conversations/{conv['id']}/note"
    assert client.get(path).status_code == 404

    created = client.put(path, json={"title": "План", "body": "# Hello\n\n- item"})
    assert created.status_code == 200
    payload = created.json()
    assert payload["title"] == "План"
    assert payload["body"] == "# Hello\n\n- item"
    assert payload["id"]

    fetched = client.get(path)
    assert fetched.status_code == 200
    assert fetched.json()["body"] == payload["body"]

    updated = client.put(path, json={"body": "вторая версия"})
    assert updated.json()["title"] == "План"
    assert updated.json()["body"] == "вторая версия"
    assert updated.json()["id"] == payload["id"]

    download = client.get(f"{path}/download")
    assert download.status_code == 200
    assert download.content == "вторая версия".encode("utf-8")
    assert "text/markdown" in download.headers["content-type"]
    assert "filename*=UTF-8''" in download.headers["content-disposition"]


def test_note_not_visible_to_other_user(client):
    _auth(client)
    conv = _create_conversation(client)
    client.put(
        f"/api/conversations/{conv['id']}/note",
        json={"body": "secret"},
    )
    register(client, email())
    assert client.get(f"/api/conversations/{conv['id']}/note").status_code == 404
    assert (
        client.put(
            f"/api/conversations/{conv['id']}/note",
            json={"body": "hack"},
        ).status_code
        == 404
    )


def test_deleting_conversation_keeps_note(client, db):
    _auth(client)
    conv = _create_conversation(client)
    put = client.put(
        f"/api/conversations/{conv['id']}/note",
        json={"title": "Черновик", "body": "keep me"},
    )
    note_id = int(put.json()["id"])
    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 200
    db.expire_all()
    leftover = db.get(Note, note_id)
    assert leftover is not None
    assert leftover.body == "keep me"
    links = db.scalars(select(ConversationNote).where(ConversationNote.note_id == note_id)).all()
    assert links == []


def test_note_tools_read_write_append(client, db):
    import asyncio
    import json

    from app.db import Conversation, User
    from app.tools import call_tool

    _auth(client)
    conv = _create_conversation(client)
    conv_row = db.get(Conversation, int(conv["id"]))
    user = db.get(User, conv_row.user_id)
    empty = json.loads(
        asyncio.run(call_tool("read_chat_note", "{}", user=user, db=db, conversation_id=conv["id"]))
    )
    assert empty == {"exists": False, "title": "", "body": ""}
    written = json.loads(
        asyncio.run(
            call_tool(
                "write_chat_note",
                json.dumps({"title": "T", "body": "a"}),
                user=user,
                db=db,
                conversation_id=conv["id"],
            )
        )
    )
    assert written["ok"] is True
    assert written["body"] == "a"
    db.expire_all()
    appended = json.loads(
        asyncio.run(
            call_tool(
                "write_chat_note",
                json.dumps({"body": "b", "mode": "append"}),
                user=user,
                db=db,
                conversation_id=conv["id"],
            )
        )
    )
    assert appended["body"].endswith("b")
    assert appended["body"].startswith("a")
    missing = json.loads(
        asyncio.run(call_tool("read_chat_note", "{}", user=user, db=db, conversation_id=None))
    )
    assert "error" in missing


def test_note_too_large(client):
    _auth(client)
    conv = _create_conversation(client)
    body = "x" * (MAX_NOTE_BODY + 1)
    response = client.put(
        f"/api/conversations/{conv['id']}/note",
        json={"body": body},
    )
    assert response.status_code == 413


def test_download_filename_sanitized():
    assert download_filename("План/релиз: v1") == "План_релиз_ v1.md"
    assert download_filename("   ") == "note.md"
