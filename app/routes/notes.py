from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.notes import (
    NOTE_TOO_LARGE,
    content_disposition,
    download_filename,
    latest_note_for_conversation,
    note_payload,
    upsert_conversation_note,
)
from app.routes.conversations import _get_owned_conversation, _require_user

router = APIRouter()


class NoteUpdate(BaseModel):
    title: str | None = None
    body: str | None = None


@router.get("/api/conversations/{conversation_id}/note")
def get_note(conversation_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conv = _get_owned_conversation(db, user, conversation_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    note = latest_note_for_conversation(db, conv.id)
    if note is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return note_payload(note)


@router.put("/api/conversations/{conversation_id}/note")
def put_note(
    conversation_id: int,
    request: Request,
    body: NoteUpdate,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conv = _get_owned_conversation(db, user, conversation_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    note, err = upsert_conversation_note(
        db,
        user,
        conv,
        title=body.title,
        body=body.body,
        mode="replace",
    )
    if err == NOTE_TOO_LARGE:
        return JSONResponse(status_code=413, content={"error": err})
    if err or note is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    db.commit()
    db.refresh(note)
    return note_payload(note)


@router.get("/api/conversations/{conversation_id}/note/download")
def download_note(conversation_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    conv = _get_owned_conversation(db, user, conversation_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    note = latest_note_for_conversation(db, conv.id)
    if note is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    filename = download_filename(note.title)
    return Response(
        content=note.body.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": content_disposition(filename)},
    )
