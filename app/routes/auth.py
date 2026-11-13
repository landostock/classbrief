from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Setting
from app.services.security import PASSWORD_HASH_SETTING, hash_password, sign_session, verify_app_password
from app.services.web_access import consume_web_access_token, valid_web_access_token

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    return request.app.state.templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    settings = get_settings()
    if not verify_app_password(password, settings, db):
        return request.app.state.templates.TemplateResponse(request, "login.html", {"error": "Das Passwort stimmt nicht."}, status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "classbrief_session",
        sign_session(settings.session_secret),
        httponly=True,
        secure=settings.production,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@router.get("/access")
async def access_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    valid = valid_web_access_token(db, token)
    return request.app.state.templates.TemplateResponse(
        request,
        "access.html",
        {"token": token if valid else "", "error": None if valid else "Dieser Zugangslink ist ungültig oder abgelaufen."},
        status_code=200 if valid else 400,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/access")
async def set_password_from_access_link(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    error = None
    if not valid_web_access_token(db, token):
        error = "Dieser Zugangslink ist ungültig oder abgelaufen."
    elif password != password_confirm:
        error = "Die beiden Passwörter stimmen nicht überein."
    elif len(password) < 12:
        error = "Bitte verwende mindestens 12 Zeichen."
    elif len(password) > 200:
        error = "Das Passwort ist zu lang."
    if error:
        return request.app.state.templates.TemplateResponse(
            request,
            "access.html",
            {"token": token if valid_web_access_token(db, token) else "", "error": error},
            status_code=400,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    if not consume_web_access_token(db, token):
        return request.app.state.templates.TemplateResponse(
            request,
            "access.html",
            {"token": "", "error": "Dieser Zugangslink ist bereits verbraucht."},
            status_code=400,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    hashed = hash_password(password)
    row = db.get(Setting, PASSWORD_HASH_SETTING)
    if row:
        row.value = hashed
    else:
        db.add(Setting(key=PASSWORD_HASH_SETTING, value=hashed))
    db.commit()
    settings = get_settings()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "classbrief_session",
        sign_session(settings.session_secret),
        httponly=True,
        secure=settings.production,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("classbrief_session")
    return response
