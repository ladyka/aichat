from __future__ import annotations

import html
from urllib.parse import quote

import markdown
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_user_from_session
from app.config import get_settings
from app.db import get_db
from app.og import plain_snippet, public_origin
from app.routes.pages import render, safe_next_path
from app.skills import (
    SKILL_TOO_LARGE,
    catalog_skill,
    catalog_url,
    copy_catalog_skill,
    create_skill,
    default_skill_ids,
    list_catalog_skills,
    list_owned_skills,
    owned_skill,
    publish_skill,
    revoke_skill_share,
    set_default_skill_ids,
    skill_is_public,
    skill_payload,
    update_skill,
)

router = APIRouter()


def _user_or_login(request: Request, db: Session, *, next_path: str):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    if user:
        return user
    dest = safe_next_path(next_path) or "/skills"
    return RedirectResponse(f"/login?next={quote(dest, safe='/')}", status_code=303)


def _render_md(text: str) -> str:
    return markdown.markdown(
        html.escape(text or ""),
        extensions=["fenced_code", "nl2br"],
    )


def _date(value) -> str:
    if value is None:
        return ""
    iso = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return iso[:10]


def _toggle_default(db, user, skill_id: int, enabled: bool) -> str | None:
    ids = default_skill_ids(db, user)
    key = str(skill_id)
    if enabled and key not in ids:
        ids.append(key)
    elif not enabled:
        ids = [item for item in ids if item != key]
    else:
        return None
    _, err = set_default_skill_ids(db, user, ids)
    return err


@router.get("/skills")
def skills_library(request: Request, db: Session = Depends(get_db)):
    user = _user_or_login(request, db, next_path="/skills")
    if isinstance(user, RedirectResponse):
        return user
    rows = list_owned_skills(db, user)
    defaults = set(default_skill_ids(db, user))
    items = []
    for skill in rows:
        payload = skill_payload(skill)
        payload["updated_day"] = _date(skill.updated_at)
        payload["is_default"] = payload["id"] in defaults
        items.append(payload)
    return render(request, "skills.html", user, items=items)


@router.get("/skills/new")
def skill_new_page(request: Request, db: Session = Depends(get_db)):
    user = _user_or_login(request, db, next_path="/skills/new")
    if isinstance(user, RedirectResponse):
        return user
    return render(
        request,
        "skill_edit.html",
        user,
        skill={"title": "", "description": "", "body": "", "id": None},
        is_public=False,
        is_default=False,
        catalog_href="",
        error=None,
        saved=False,
        copied_from=False,
    )


@router.post("/skills/new")
def skill_new_submit(
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    body: str = Form(""),
    public: str = Form(""),
    default: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _user_or_login(request, db, next_path="/skills/new")
    if isinstance(user, RedirectResponse):
        return user
    skill, err = create_skill(db, user, title=title, description=description, body=body)
    if err or skill is None:
        return render(
            request,
            "skill_edit.html",
            user,
            status_code=413 if err == SKILL_TOO_LARGE else 400,
            skill={
                "title": title,
                "description": description,
                "body": body,
            },
            is_public=public == "on",
            is_default=default == "on",
            catalog_href="",
            error=err or SKILL_TOO_LARGE,
            saved=False,
        )
    if public == "on":
        publish_skill(db, skill)
    if default == "on":
        err = _toggle_default(db, user, skill.id, True)
        if err:
            db.commit()
            return RedirectResponse(f"/skills/{skill.id}?error={quote(err)}", status_code=303)
    db.commit()
    return RedirectResponse(f"/skills/{skill.id}", status_code=303)


@router.get("/skills/{skill_id}")
def skill_edit_page(skill_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_login(request, db, next_path=f"/skills/{skill_id}")
    if isinstance(user, RedirectResponse):
        return user
    skill = owned_skill(db, user, skill_id)
    if skill is None:
        return render(
            request,
            "skill_missing.html",
            user,
            status_code=404,
            og_description="Навык не найден.",
            robots="noindex",
        )
    is_public = skill_is_public(skill)
    return render(
        request,
        "skill_edit.html",
        user,
        skill=skill_payload(skill, public=is_public),
        is_public=is_public,
        is_default=str(skill.id) in set(default_skill_ids(db, user)),
        catalog_href=public_origin(request) + catalog_url(skill.id) if is_public else "",
        error=request.query_params.get("error"),
        saved=request.query_params.get("saved") == "1",
        copied_from=skill.parent_id is not None,
    )


@router.post("/skills/{skill_id}")
def skill_edit_submit(
    skill_id: int,
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    body: str = Form(""),
    public: str = Form(""),
    default: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _user_or_login(request, db, next_path=f"/skills/{skill_id}")
    if isinstance(user, RedirectResponse):
        return user
    skill = owned_skill(db, user, skill_id)
    if skill is None:
        return render(
            request,
            "skill_missing.html",
            user,
            status_code=404,
            robots="noindex",
        )
    err = update_skill(db, skill, title=title, description=description, body=body)
    if err:
        is_public = skill_is_public(skill)
        return render(
            request,
            "skill_edit.html",
            user,
            status_code=413 if err == SKILL_TOO_LARGE else 400,
            skill={
                **skill_payload(skill, public=is_public),
                "title": title,
                "description": description,
                "body": body,
            },
            is_public=public == "on",
            is_default=default == "on",
            catalog_href=public_origin(request) + catalog_url(skill.id) if is_public else "",
            error=err,
            saved=False,
            copied_from=skill.parent_id is not None,
        )
    if public == "on":
        publish_skill(db, skill)
    else:
        revoke_skill_share(db, skill)
    default_err = _toggle_default(db, user, skill.id, default == "on")
    db.commit()
    if default_err:
        return RedirectResponse(
            f"/skills/{skill.id}?error={quote(default_err)}",
            status_code=303,
        )
    return RedirectResponse(f"/skills/{skill.id}?saved=1", status_code=303)


@router.post("/skills/{skill_id}/delete")
def skill_delete(skill_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_login(request, db, next_path="/skills")
    if isinstance(user, RedirectResponse):
        return user
    skill = owned_skill(db, user, skill_id)
    if skill is not None:
        db.delete(skill)
        db.commit()
    return RedirectResponse("/skills", status_code=303)


@router.get("/catalog")
def catalog_page(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    rows = list_catalog_skills(db)
    items = [
        {
            "id": str(skill.id),
            "title": skill.title,
            "description": skill.description,
            "updated_day": _date(skill.updated_at),
        }
        for skill in rows
    ]
    return render(
        request,
        "catalog.html",
        user,
        items=items,
        og_description="Публичные навыки aichat: инструкции, которые можно скопировать себе.",
    )


@router.get("/catalog/skills/{skill_id}")
def catalog_card_page(skill_id: int, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    user = get_user_from_session(db, request.cookies.get(settings.session_cookie))
    skill = catalog_skill(db, skill_id)
    if skill is None:
        return render(
            request,
            "skill_missing.html",
            user,
            status_code=404,
            og_description="Этот навык скрыт, отозван или не существует.",
            robots="noindex",
        )
    description = (skill.description or "").strip() or plain_snippet(skill.body)
    return render(
        request,
        "catalog_skill.html",
        user,
        skill_id=str(skill.id),
        title=skill.title,
        description=skill.description,
        body_html=_render_md(skill.body),
        updated_day=_date(skill.updated_at),
        login_next=f"/catalog/skills/{skill.id}",
        og_type="article",
        og_description=description or "Публичный навык aichat",
    )


@router.post("/catalog/skills/{skill_id}/copy")
def catalog_copy_submit(skill_id: int, request: Request, db: Session = Depends(get_db)):
    next_path = f"/catalog/skills/{skill_id}"
    user = _user_or_login(request, db, next_path=next_path)
    if isinstance(user, RedirectResponse):
        return user
    source = catalog_skill(db, skill_id)
    if source is None:
        return render(
            request,
            "skill_missing.html",
            user,
            status_code=404,
            robots="noindex",
        )
    copy, err = copy_catalog_skill(db, user, source)
    if err or copy is None:
        return render(
            request,
            "catalog_skill.html",
            user,
            status_code=400,
            skill_id=str(source.id),
            title=source.title,
            description=source.description,
            body_html=_render_md(source.body),
            updated_day=_date(source.updated_at),
            login_next=next_path,
            error=err or SKILL_TOO_LARGE,
            og_description=source.description or "Публичный навык aichat",
        )
    db.commit()
    return RedirectResponse(f"/skills/{copy.id}", status_code=303)
