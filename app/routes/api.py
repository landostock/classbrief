from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import BriefingClarification, Lesson, PushSubscription, Subject
from app.services.chatgpt_plan import (
    ChatGPTPlanError,
    active_connection,
    get_pairing,
    import_connection,
    provider_mode,
)
from app.services.lesson_preparation import AlreadyProcessing, BudgetExceeded, clarification_payload, prepare_clarifications, prepare_lesson
from app.services.schedule import BERLIN, upcoming_lessons

router = APIRouter(prefix="/api")


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="ignore")
    p256dh: str
    auth: str


class PushSubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    endpoint: str
    keys: PushKeys


class ChatGPTPairingImport(BaseModel):
    client_id: str
    id_token: str
    access_token: str
    refresh_token: str
    expires_in: int = 3600
    scope: str
    nonce: str


class ClarificationAnswerInput(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    skip: bool = False


def _pairing_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Kopplungscode fehlt")
    return authorization[7:]


@router.get("/chatgpt/pairing")
async def chatgpt_pairing_info(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    try:
        pairing = get_pairing(db, _pairing_token(authorization))
    except ChatGPTPlanError as exc:
        raise HTTPException(401, str(exc)) from exc
    connection = active_connection(db)
    return {
        "host_id": pairing.host_id,
        "client_id": connection.client_id if connection and connection.host_id == pairing.host_id else None,
    }


@router.post("/chatgpt/pairing")
async def chatgpt_pairing_import(
    payload: ChatGPTPairingImport,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    try:
        pairing = get_pairing(db, _pairing_token(authorization))
        connection = await import_connection(
            db,
            get_settings(),
            pairing=pairing,
            client_id=payload.client_id,
            id_token=payload.id_token,
            access_token=payload.access_token,
            refresh_token=payload.refresh_token,
            expires_in=payload.expires_in,
            scope=payload.scope,
            nonce=payload.nonce,
        )
    except ChatGPTPlanError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "connected", "email": connection.email, "message": "Dein ChatGPT-Abo ist jetzt mit ClassBrief verbunden."}


@router.post("/subjects/{subject_id}/prepare")
async def prepare_subject(subject_id: str, db: Session = Depends(get_db)):
    subject = db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(404)
    if not subject.briefing_enabled:
        raise HTTPException(409, "Für dieses Fach sind keine Briefings vorgesehen.")
    if provider_mode(db, get_settings()) == "plugin":
        raise HTTPException(409, "Plugin-Modus aktiv: Starte das Briefing in ChatGPT mit ClassBrief.")
    now = datetime.now(BERLIN)
    lesson = next((item for item in upcoming_lessons(db, now, days=21, limit=50) if item.subject_id == subject_id), None)
    if lesson is None:
        lesson = Lesson(subject_id=subject_id, scheduled_start=now + timedelta(minutes=subject.preparation_minutes), scheduled_end=now + timedelta(minutes=subject.preparation_minutes + 45), source="manual")
        db.add(lesson)
        db.commit()
    try:
        status, briefing = await prepare_lesson(db, lesson, get_settings(), trigger="manual", force_budget=True)
        return {"status": status, "briefing_id": briefing.id}
    except AlreadyProcessing:
        return {"status": "already_processing"}
    except BudgetExceeded:
        raise HTTPException(402, "Monatliches KI-Budget erreicht")


def _upcoming_subject_lesson(db: Session, subject: Subject) -> Lesson:
    now = datetime.now(BERLIN)
    lesson = next((item for item in upcoming_lessons(db, now, days=21, limit=50) if item.subject_id == subject.id), None)
    if lesson is None:
        lesson = Lesson(
            subject_id=subject.id,
            scheduled_start=now + timedelta(minutes=subject.preparation_minutes),
            scheduled_end=now + timedelta(minutes=subject.preparation_minutes + 45),
            source="manual",
        )
        db.add(lesson)
        db.commit()
        db.refresh(lesson)
    return lesson


async def _prepare_clarification_response(lesson: Lesson, db: Session) -> dict:
    if lesson.status == "cancelled" or not lesson.subject.briefing_enabled:
        raise HTTPException(409, "Für diese Stunde ist kein Briefing vorgesehen.")
    settings = get_settings()
    if provider_mode(db, settings) == "plugin":
        raise HTTPException(409, "Verbinde zuerst dein ChatGPT-Abo in den Einstellungen.")
    try:
        return await prepare_clarifications(db, lesson, settings)
    except AlreadyProcessing:
        return {"status": "already_processing"}


@router.post("/subjects/{subject_id}/clarifications")
async def inspect_subject_clarifications(subject_id: str, db: Session = Depends(get_db)):
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, "Fach nicht gefunden.")
    return await _prepare_clarification_response(_upcoming_subject_lesson(db, subject), db)


@router.post("/lessons/{lesson_id}/clarifications")
async def inspect_lesson_clarifications(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(404, "Diese Stunde ist nicht mehr verfügbar.")
    return await _prepare_clarification_response(lesson, db)


@router.get("/clarifications/pending")
async def pending_clarification(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    items = list(
        db.scalars(
            select(BriefingClarification)
            .where(BriefingClarification.status == "pending")
            .order_by(BriefingClarification.created_at.desc())
            .limit(10)
        )
    )
    for item in items:
        expires = item.expires_at if item.expires_at.tzinfo else item.expires_at.replace(tzinfo=timezone.utc)
        if expires > now and item.lesson.status != "cancelled":
            return {"status": "questions", "clarification": clarification_payload(item)}
        if item.status == "pending":
            item.status = "expired"
    db.commit()
    return {"status": "none"}


@router.post("/clarifications/{clarification_id}/answer")
async def answer_clarification(clarification_id: int, payload: ClarificationAnswerInput, db: Session = Depends(get_db)):
    item = db.get(BriefingClarification, clarification_id)
    if item is None or item.status != "pending":
        raise HTTPException(409, "Diese Rückfrage ist nicht mehr offen.")
    expires = item.expires_at if item.expires_at.tzinfo else item.expires_at.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        item.status = "expired"
        db.commit()
        raise HTTPException(409, "Diese Rückfrage ist abgelaufen.")
    questions = json.loads(item.questions_json or "[]")
    answers: list[dict] = []
    if not payload.skip:
        for question in questions:
            selected_id = payload.answers.get(question["id"])
            option = next((entry for entry in question["options"] if entry["id"] == selected_id), None)
            if option is None:
                raise HTTPException(422, "Bitte beantworte jede Frage oder wähle ‚Ohne Antworten fortfahren‘.")
            answers.append({
                "question": question["prompt"],
                "answer": option["label"],
                "certainty": "unknown" if option["id"] == "unknown" else "user_confirmed",
            })
    item.answers_json = json.dumps(answers, ensure_ascii=False)
    item.status = "skipped" if payload.skip else "answered"
    item.answered_at = datetime.now(timezone.utc)
    db.commit()
    try:
        status, briefing = await prepare_lesson(
            db,
            item.lesson,
            get_settings(),
            trigger="manual",
            force_budget=True,
            clarification_answers=answers or None,
        )
    except AlreadyProcessing:
        item.status = "pending"
        db.commit()
        return {"status": "already_processing"}
    except BudgetExceeded:
        item.status = "pending"
        db.commit()
        raise HTTPException(402, "Monatliches KI-Budget erreicht")
    except Exception:
        item.status = "pending"
        db.commit()
        raise
    item.status = "completed"
    db.commit()
    return {"status": status, "briefing_id": briefing.id}


@router.post("/lessons/{lesson_id}/prepare")
async def prepare_specific_lesson(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.get(Lesson, lesson_id)
    if lesson is None or lesson.status == "cancelled":
        raise HTTPException(404, "Diese Stunde ist nicht mehr verfügbar.")
    if not lesson.subject.briefing_enabled:
        raise HTTPException(409, "Für dieses Fach sind keine Briefings vorgesehen.")
    settings = get_settings()
    if provider_mode(db, settings) == "plugin":
        raise HTTPException(409, "Verbinde zuerst dein ChatGPT-Abo in den Einstellungen.")
    try:
        status, briefing = await prepare_lesson(db, lesson, settings, trigger="manual", force_budget=True)
        return {"status": status, "briefing_id": briefing.id}
    except AlreadyProcessing:
        return {"status": "already_processing"}
    except BudgetExceeded:
        raise HTTPException(402, "Monatliches KI-Budget erreicht")


@router.get("/system/status")
async def system_status(db: Session = Depends(get_db)):
    settings = get_settings()
    mode = provider_mode(db, settings)
    return {
        "status": "ok",
        "iserv_configured": bool(settings.iserv_webdav_url and settings.iserv_username),
        "ai_provider": mode,
        "ai_configured": mode in {"plugin", "chatgpt_plan"} or bool(settings.openai_api_key),
    }


@router.get("/push/public-key")
async def push_public_key():
    return {"public_key": get_settings().vapid_public_key}


@router.post("/push/subscribe")
async def push_subscribe(payload: PushSubscriptionInput, db: Session = Depends(get_db)):
    current = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint))
    if current:
        current.p256dh = payload.keys.p256dh
        current.auth = payload.keys.auth
    else:
        db.add(PushSubscription(endpoint=payload.endpoint, p256dh=payload.keys.p256dh, auth=payload.keys.auth))
    db.commit()
    return {"status": "subscribed"}
