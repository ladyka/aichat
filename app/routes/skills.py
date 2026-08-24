from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.routes.conversations import _require_user
from app.skills import (
    SKILL_TOO_LARGE,
    catalog_detail,
    catalog_skill,
    catalog_summary,
    copy_catalog_skill,
    create_skill,
    list_catalog_skills,
    list_owned_skills,
    owned_skill,
    parse_skill_id,
    publish_skill,
    revoke_skill_share,
    share_payload,
    skill_is_public,
    skill_payload,
    update_skill,
)

router = APIRouter()


class SkillWrite(BaseModel):
    title: str | None = None
    description: str | None = None
    body: str | None = None


def _skill_id_or_404(raw: str) -> int | JSONResponse:
    skill_id = parse_skill_id(raw)
    if skill_id is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return skill_id


def _error_status(err: str) -> JSONResponse:
    status = 413 if err == SKILL_TOO_LARGE else 400
    return JSONResponse(status_code=status, content={"error": err})


@router.get("/api/skills")
def get_skills(request: Request, q: str | None = None, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    rows = list_owned_skills(db, user, q=q)
    return {"data": [skill_payload(skill) for skill in rows]}


@router.post("/api/skills")
def post_skill(request: Request, body: SkillWrite, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    skill, err = create_skill(
        db,
        user,
        title=body.title,
        description=body.description,
        body=body.body,
    )
    if err or skill is None:
        return _error_status(err or SKILL_TOO_LARGE)
    db.commit()
    db.refresh(skill)
    return skill_payload(skill, public=False)


@router.get("/api/skills/{skill_id}")
def get_skill(skill_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    parsed = _skill_id_or_404(skill_id)
    if isinstance(parsed, JSONResponse):
        return parsed
    skill = owned_skill(db, user, parsed)
    if skill is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return skill_payload(skill)


@router.patch("/api/skills/{skill_id}")
def patch_skill(
    skill_id: str,
    request: Request,
    body: SkillWrite,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    parsed = _skill_id_or_404(skill_id)
    if isinstance(parsed, JSONResponse):
        return parsed
    skill = owned_skill(db, user, parsed)
    if skill is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    err = update_skill(
        db,
        skill,
        title=body.title,
        description=body.description,
        body=body.body,
    )
    if err:
        return _error_status(err)
    db.commit()
    db.refresh(skill)
    return skill_payload(skill)


@router.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    parsed = _skill_id_or_404(skill_id)
    if isinstance(parsed, JSONResponse):
        return parsed
    skill = owned_skill(db, user, parsed)
    if skill is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    db.delete(skill)
    db.commit()
    return {"ok": True}


@router.get("/api/skills/{skill_id}/share")
def get_skill_share(skill_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    parsed = _skill_id_or_404(skill_id)
    if isinstance(parsed, JSONResponse):
        return parsed
    skill = owned_skill(db, user, parsed)
    if skill is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return share_payload(skill, public=skill_is_public(skill))


@router.post("/api/skills/{skill_id}/share")
def post_skill_share(skill_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    parsed = _skill_id_or_404(skill_id)
    if isinstance(parsed, JSONResponse):
        return parsed
    skill = owned_skill(db, user, parsed)
    if skill is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    publish_skill(db, skill)
    db.commit()
    db.refresh(skill)
    return share_payload(skill, public=True)


@router.post("/api/skills/{skill_id}/share/revoke")
def post_skill_share_revoke(skill_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    parsed = _skill_id_or_404(skill_id)
    if isinstance(parsed, JSONResponse):
        return parsed
    skill = owned_skill(db, user, parsed)
    if skill is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    revoke_skill_share(db, skill)
    db.commit()
    return {"public": False}


@router.get("/api/catalog/skills")
def get_catalog_skills(db: Session = Depends(get_db)):
    rows = list_catalog_skills(db)
    return {"data": [catalog_summary(skill) for skill in rows]}


@router.get("/api/catalog/skills/{skill_id}")
def get_catalog_skill(skill_id: str, db: Session = Depends(get_db)):
    parsed = _skill_id_or_404(skill_id)
    if isinstance(parsed, JSONResponse):
        return parsed
    skill = catalog_skill(db, parsed)
    if skill is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return catalog_detail(skill)


@router.post("/api/catalog/skills/{skill_id}/copy")
def post_catalog_skill_copy(
    skill_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    if isinstance(user, JSONResponse):
        return user
    parsed = _skill_id_or_404(skill_id)
    if isinstance(parsed, JSONResponse):
        return parsed
    source = catalog_skill(db, parsed)
    if source is None:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    copy, err = copy_catalog_skill(db, user, source)
    if err or copy is None:
        return _error_status(err or SKILL_TOO_LARGE)
    db.commit()
    db.refresh(copy)
    return skill_payload(copy, public=False)
