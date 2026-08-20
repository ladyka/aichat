from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Conversation, ConversationNote, Note, User

MAX_NOTE_BODY = 512 * 1024
DEFAULT_NOTE_TITLE = "Заметка"
NOTE_TOO_LARGE = "Заметка слишком большая"


def parse_conversation_id(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def note_payload(note: Note) -> dict[str, Any]:
    return {
        "id": str(note.id),
        "title": note.title,
        "body": note.body,
        "created_at": _iso(note.created_at),
        "updated_at": _iso(note.updated_at),
    }


def normalize_title(title: str | None, fallback: str = DEFAULT_NOTE_TITLE) -> str:
    value = (title or "").strip()[:200]
    return value or fallback


def owned_conversation(db: Session, user: User, conversation_id: int) -> Conversation | None:
    return db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
        )
    )


def latest_note_for_conversation(db: Session, conversation_id: int) -> Note | None:
    link = db.scalar(
        select(ConversationNote)
        .where(ConversationNote.conversation_id == conversation_id)
        .order_by(ConversationNote.created_at.desc(), ConversationNote.note_id.desc())
        .limit(1)
    )
    if link is None:
        return None
    return db.get(Note, link.note_id)


def _validate_body_len(body: str) -> str | None:
    if len(body.encode("utf-8")) > MAX_NOTE_BODY:
        return NOTE_TOO_LARGE
    return None


def upsert_conversation_note(
    db: Session,
    user: User,
    conversation: Conversation,
    *,
    title: str | None = None,
    body: str | None = None,
    mode: str = "replace",
) -> tuple[Note | None, str | None]:
    """Create or update the v1 note for a chat. Returns (note, error)."""
    if conversation.user_id != user.id:
        return None, "Not found"
    now = datetime.now(timezone.utc)
    note = latest_note_for_conversation(db, conversation.id)
    write_mode = (mode or "replace").strip().lower()
    if write_mode not in ("replace", "append"):
        write_mode = "replace"

    if note is None:
        new_body = body if body is not None else ""
        err = _validate_body_len(new_body)
        if err:
            return None, err
        note = Note(
            user_id=user.id,
            title=normalize_title(title),
            body=new_body,
            created_at=now,
            updated_at=now,
        )
        db.add(note)
        db.flush()
        db.add(
            ConversationNote(
                conversation_id=conversation.id,
                note_id=note.id,
                created_at=now,
            )
        )
        return note, None

    if title is not None:
        note.title = normalize_title(title, fallback=note.title)
    if body is not None:
        if write_mode == "append":
            if body and note.body and not note.body.endswith("\n"):
                new_body = f"{note.body}\n{body}"
            else:
                new_body = f"{note.body}{body}"
        else:
            new_body = body
        err = _validate_body_len(new_body)
        if err:
            return note, err
        note.body = new_body
    note.updated_at = now
    return note, None


def download_filename(title: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\r\n]+', "_", (title or "").strip())
    base = re.sub(r"\s+", " ", base).strip(" .") or "note"
    return f"{base[:80]}.md"


def content_disposition(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "note.md"
    encoded = quote(filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"
