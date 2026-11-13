from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AIUsage, AutomationRule, Briefing, BriefingMaterial, FileRecord, ProcessingRun, ScheduleEntry, ScheduleException, Setting, Subject, SubjectMemory
from app.services.automation import (
    automation_sync_state,
    build_automation_plan,
    create_recommended_rules,
    describe_rule,
    latest_runs,
    serialize_weekdays,
    validate_rule,
)
from app.services.cost_tracker import current_spend, month_start
from app.services.chatgpt_plan import active_connection, create_pairing, disconnect, provider_mode
from app.services.schedule import BERLIN, as_berlin, lessons_for_range, upcoming_lessons

router = APIRouter()
COLORS = ["indigo", "cyan", "amber", "rose", "emerald", "violet"]
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def render(request: Request, template: str, context: dict):
    context.update({"weekdays": WEEKDAYS, "colors": COLORS, "now": datetime.now(BERLIN)})
    return request.app.state.templates.TemplateResponse(request, template, context)


def _briefing_is_empty(briefing: Briefing) -> bool:
    try:
        deep = json.loads(briefing.deep_brief_json or "{}")
    except json.JSONDecodeError:
        deep = {}

    # "Keine neuen Materialien" ist kein leeres Briefing: Der bestätigte
    # Unterrichtsstand, Anschlüsse und offene Fragen bleiben eine sinnvolle
    # Vorbereitung. Leer bedeutet ausschließlich, dass technisch kein
    # darstellbarer Inhalt gespeichert wurde.
    useful = [
        briefing.probable_topic,
        briefing.quick_brief,
        briefing.medium_brief,
        deep.get("summary"),
        *(deep.get("prior_knowledge") or []),
        *(deep.get("new_materials") or []),
        *(deep.get("important_terms") or []),
        *(deep.get("likely_tasks") or []),
        *(deep.get("pitfalls") or []),
        *(deep.get("connections") or []),
        *(deep.get("answer_cards") or []),
        deep.get("presentation"),
    ]
    return not any(str(item).strip() for item in useful if item)


def _briefing_has_new_material(briefing: Briefing) -> bool:
    try:
        deep = json.loads(briefing.deep_brief_json or "{}")
    except json.JSONDecodeError:
        return False
    return bool(deep.get("new_materials") or deep.get("sources"))


@router.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    now = datetime.now(BERLIN)
    lessons = lessons_for_range(db, now.date(), now.date())
    lesson_ids = [lesson.id for lesson in lessons]
    briefing_by_lesson: dict[int, Briefing] = {}
    if lesson_ids:
        for item in db.scalars(
            select(Briefing)
            .where(Briefing.lesson_id.in_(lesson_ids), Briefing.status == "ready")
            .order_by(Briefing.created_at.desc())
        ):
            briefing_by_lesson.setdefault(item.lesson_id, item)
    next_lesson = next((lesson for lesson in lessons if as_berlin(lesson.scheduled_end) > now), None)
    lesson_rows = []
    for lesson in lessons:
        start_at = as_berlin(lesson.scheduled_start)
        end_at = as_berlin(lesson.scheduled_end)
        state = "past" if end_at <= now else ("live" if start_at <= now < end_at else "upcoming")
        lesson_rows.append({"lesson": lesson, "start": start_at, "end": end_at, "state": state, "briefing": briefing_by_lesson.get(lesson.id)})
    alert = db.scalar(select(ProcessingRun).where(ProcessingRun.status == "failed").order_by(ProcessingRun.completed_at.desc()))
    recent_briefings = list(
        db.scalars(
            select(Briefing)
            .where(Briefing.status == "ready")
            .order_by(Briefing.created_at.desc())
            .limit(6)
        )
    )
    from app.config import get_settings
    settings = get_settings()
    return render(
        request,
        "dashboard.html",
        {
            "lessons": lessons,
            "lesson_rows": lesson_rows,
            "next_lesson": next_lesson,
            "briefing_by_lesson": briefing_by_lesson,
            "alert": alert,
            "recent_briefings": recent_briefings,
            "provider": provider_mode(db, settings),
            "chatgpt_connection": active_connection(db),
        },
    )


@router.get("/schedule")
async def schedule_overview(request: Request, week: int = 0, db: Session = Depends(get_db)):
    offset = max(-8, min(12, week))
    today = datetime.now(BERLIN).date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    friday = monday + timedelta(days=4)
    lessons = lessons_for_range(db, monday, friday)
    grouped_lessons = {monday + timedelta(days=index): [] for index in range(5)}
    for lesson in lessons:
        grouped_lessons.setdefault(as_berlin(lesson.scheduled_start).date(), []).append(lesson)
    lesson_ids = [item.id for item in lessons]
    briefing_by_lesson: dict[int, Briefing] = {}
    if lesson_ids:
        for item in db.scalars(
            select(Briefing)
            .where(Briefing.lesson_id.in_(lesson_ids), Briefing.status == "ready")
            .order_by(Briefing.created_at.desc())
        ):
            briefing_by_lesson.setdefault(item.lesson_id, item)
    now_at = datetime.now(BERLIN)
    grouped = {}
    for day, items in grouped_lessons.items():
        grouped[day] = []
        for lesson in items:
            start_at = as_berlin(lesson.scheduled_start)
            end_at = as_berlin(lesson.scheduled_end)
            state = "past" if end_at <= now_at else ("live" if start_at <= now_at < end_at else "upcoming")
            grouped[day].append({"lesson": lesson, "start": start_at, "end": end_at, "state": state, "briefing": briefing_by_lesson.get(lesson.id)})
    return render(
        request,
        "schedule.html",
        {
            "week_offset": offset,
            "week_start": monday,
            "week_end": friday,
            "today": today,
            "days": grouped,
            "briefing_by_lesson": briefing_by_lesson,
            "provider": provider_mode(db, get_settings()),
        },
    )


@router.get("/briefings")
async def briefing_archive(request: Request, subject: str = "", db: Session = Depends(get_db)):
    query = select(Briefing).where(Briefing.status == "ready").order_by(Briefing.created_at.desc()).limit(100)
    if subject:
        query = query.where(Briefing.subject_id == subject)
    briefings = list(db.scalars(query))
    subjects = list(db.scalars(select(Subject).order_by(Subject.name)))
    return render(request, "briefings.html", {"briefings": briefings, "subjects": subjects, "selected_subject": subject, "briefing_is_empty": _briefing_is_empty, "briefing_has_new_material": _briefing_has_new_material})


@router.get("/briefings/{briefing_id}")
async def briefing_page(briefing_id: int, request: Request, db: Session = Depends(get_db)):
    briefing = db.get(Briefing, briefing_id)
    if not briefing:
        raise HTTPException(404)
    deep = json.loads(briefing.deep_brief_json)
    return render(request, "briefing.html", {"briefing": briefing, "deep": deep, "is_empty": _briefing_is_empty(briefing), "has_new_material": _briefing_has_new_material(briefing)})


@router.post("/briefings/{briefing_id}/delete")
async def delete_briefing(briefing_id: int, db: Session = Depends(get_db)):
    briefing = db.get(Briefing, briefing_id)
    if not briefing:
        raise HTTPException(404)
    materials = list(db.scalars(select(BriefingMaterial).where(BriefingMaterial.briefing_id == briefing_id)))
    for material in materials:
        still_used = db.scalar(
            select(func.count())
            .select_from(BriefingMaterial)
            .where(BriefingMaterial.sha256 == material.sha256, BriefingMaterial.briefing_id != briefing_id)
        ) or 0
        if not still_used:
            record = db.get(FileRecord, material.file_id)
            if record and record.processing_status == "already_briefed":
                record.processing_status = "context_ready"
    db.execute(delete(BriefingMaterial).where(BriefingMaterial.briefing_id == briefing_id))
    db.delete(briefing)
    db.commit()
    return RedirectResponse("/briefings", status_code=303)


@router.post("/briefings/{briefing_id}/feedback")
async def briefing_feedback(briefing_id: int, feedback: str = Form(...), note: str = Form(""), db: Session = Depends(get_db)):
    if feedback not in {"yes", "partial", "no"}:
        raise HTTPException(400)
    briefing = db.get(Briefing, briefing_id)
    if not briefing:
        raise HTTPException(404)
    briefing.feedback = feedback
    briefing.feedback_note = note[:2000] or None
    if feedback in {"yes", "partial"}:
        memory = db.get(SubjectMemory, briefing.subject_id)
        if memory is None:
            memory = SubjectMemory(subject_id=briefing.subject_id, memory_json="{}", version=1)
            db.add(memory)
        data = json.loads(memory.memory_json or "{}")
        deep = json.loads(briefing.deep_brief_json)
        topics = list(dict.fromkeys([*(data.get("recent_topics") or []), briefing.probable_topic]))[-10:]
        confirmed_topics = list(dict.fromkeys([*(data.get("confirmed_topics") or []), briefing.probable_topic]))[-20:]
        data["confirmed_topics"] = confirmed_topics
        data["last_confirmed_topic"] = briefing.probable_topic
        if feedback == "yes":
            data["current_unit"] = briefing.probable_topic
            data["recent_topics"] = topics
            data["important_concepts"] = deep.get("important_terms", [])[-15:]
            data["current_transition"] = deep.get("summary", "")[:500]
        elif note.strip():
            data["current_unit"] = note.strip()[:300]
            data["recent_topics"] = topics
        data.setdefault("open_questions", [])
        data["last_confirmed_at"] = briefing.lesson.scheduled_end.isoformat()
        memory.memory_json = json.dumps(data, ensure_ascii=False)
        memory.version += 1
        memory.updated_at = datetime.now(timezone.utc)
        memory.confirmed_until = briefing.lesson.scheduled_end
    db.commit()
    return RedirectResponse(f"/briefings/{briefing_id}#feedback", status_code=303)


@router.get("/subjects/{subject_id}")
async def subject_page(subject_id: str, request: Request, db: Session = Depends(get_db)):
    from app.config import get_settings
    subject = db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(404)
    briefings = list(db.scalars(select(Briefing).where(Briefing.subject_id == subject_id).order_by(Briefing.created_at.desc()).limit(8)))
    files = list(db.scalars(select(FileRecord).where(FileRecord.subject_id == subject_id).order_by(FileRecord.last_seen_at.desc()).limit(12)))
    memory = db.get(SubjectMemory, subject_id)
    future = [lesson for lesson in upcoming_lessons(db, datetime.now(BERLIN), days=21, limit=30) if lesson.subject_id == subject_id]
    settings = get_settings()
    return render(request, "subject.html", {"subject": subject, "briefings": briefings, "files": files, "memory": json.loads(memory.memory_json) if memory else {}, "next_lesson": future[0] if future else None, "provider": provider_mode(db, settings)})


@router.get("/settings/general")
async def settings_general(request: Request):
    return render(request, "settings/general.html", {})


@router.get("/settings/subjects")
async def settings_subjects(request: Request, db: Session = Depends(get_db)):
    return render(request, "settings/subjects.html", {"subjects": list(db.scalars(select(Subject).order_by(Subject.name)))})


@router.post("/settings/subjects")
async def add_subject(
    name: str = Form(...), short_name: str = Form(...), webdav_path: str = Form(...),
    preparation_minutes: int = Form(20), color_identifier: str = Form("indigo"), db: Session = Depends(get_db),
):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")).strip("-")[:80]
    if not slug or db.get(Subject, slug):
        slug = f"{slug or 'fach'}-{int(datetime.now().timestamp())}"
    db.add(Subject(id=slug, name=name.strip()[:160], short_name=short_name.strip()[:24], webdav_path="/" + webdav_path.strip("/") + "/", preparation_minutes=max(1, min(preparation_minutes, 180)), color_identifier=color_identifier if color_identifier in COLORS else "indigo"))
    db.commit()
    return RedirectResponse("/settings/subjects", status_code=303)


@router.post("/settings/subjects/{subject_id}/toggle")
async def toggle_subject(subject_id: str, db: Session = Depends(get_db)):
    subject = db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(404)
    subject.enabled = not subject.enabled
    db.commit()
    return RedirectResponse("/settings/subjects", status_code=303)


@router.post("/settings/subjects/{subject_id}/briefing-toggle")
async def toggle_subject_briefings(subject_id: str, db: Session = Depends(get_db)):
    subject = db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(404)
    subject.briefing_enabled = not subject.briefing_enabled
    db.commit()
    return RedirectResponse("/settings/subjects", status_code=303)


@router.get("/settings/schedule")
async def settings_schedule(request: Request, db: Session = Depends(get_db)):
    entries = list(db.scalars(select(ScheduleEntry).order_by(ScheduleEntry.weekday, ScheduleEntry.start_time)))
    subjects = list(db.scalars(select(Subject).where(Subject.enabled.is_(True)).order_by(Subject.name)))
    return render(request, "settings/schedule.html", {"entries": entries, "subjects": subjects})


@router.post("/settings/schedule")
async def add_schedule(subject_id: str = Form(...), weekday: int = Form(...), start_time: time = Form(...), end_time: time = Form(...), db: Session = Depends(get_db)):
    if start_time >= end_time or weekday not in range(7) or not db.get(Subject, subject_id):
        raise HTTPException(400, "Ungültige Unterrichtszeit")
    db.add(ScheduleEntry(subject_id=subject_id, weekday=weekday, start_time=start_time, end_time=end_time))
    db.commit()
    return RedirectResponse("/settings/schedule", status_code=303)


@router.post("/settings/schedule/{entry_id}/delete")
async def delete_schedule(entry_id: int, db: Session = Depends(get_db)):
    entry = db.get(ScheduleEntry, entry_id)
    if entry:
        db.delete(entry)
        db.commit()
    return RedirectResponse("/settings/schedule", status_code=303)


@router.get("/settings/exceptions")
async def settings_exceptions(request: Request, db: Session = Depends(get_db)):
    exceptions = list(db.scalars(select(ScheduleException).order_by(ScheduleException.exception_date.desc()).limit(50)))
    entries = list(db.scalars(select(ScheduleEntry).order_by(ScheduleEntry.weekday, ScheduleEntry.start_time)))
    subjects = list(db.scalars(select(Subject).where(Subject.enabled.is_(True)).order_by(Subject.name)))
    return render(request, "settings/exceptions.html", {"exceptions": exceptions, "entries": entries, "subjects": subjects})


@router.get("/settings/automations")
async def settings_automations(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    rules = list(db.scalars(select(AutomationRule).order_by(AutomationRule.id)))
    subjects = list(db.scalars(select(Subject).where(Subject.enabled.is_(True)).order_by(Subject.name)))
    plan = build_automation_plan(db)
    sync = automation_sync_state(db, plan)
    runs = latest_runs(db, [task["automation_key"] for task in plan["tasks"]])
    rule_runs = {}
    for rule in rules:
        matching = [
            runs[task["automation_key"]]
            for task in plan["tasks"]
            if runs.get(task["automation_key"])
            and any(slot.get("rule_id") == rule.id for slot in task["selector"].get("slots", []))
        ]
        if matching:
            rule_runs[rule.id] = max(matching, key=lambda item: item.created_at)
    return render(
        request,
        "settings/automations.html",
        {
            "rules": rules,
            "subjects": subjects,
            "plan": plan,
            "sync": sync,
            "provider": provider_mode(db, settings),
            "rule_runs": rule_runs,
            "describe_rule": describe_rule,
            "sync_prompt": "Verwende ClassBrief und synchronisiere jetzt meinen vollständigen Automatikplan mit geplanten Cloud-Aufgaben. Entferne dabei veraltete ClassBrief-Aufgaben.",
        },
    )


def _apply_automation_form(
    rule: AutomationRule,
    *,
    name: str,
    rule_type: str,
    weekdays: list[int],
    run_time: str,
    target_start_time: str,
    subject_id: str,
    lead_minutes: int,
) -> None:
    parse_time = lambda raw: time.fromisoformat(raw) if raw else None
    rule.name = name.strip()[:160]
    rule.rule_type = rule_type
    rule.weekdays = serialize_weekdays(weekdays)
    rule.run_time = parse_time(run_time) if rule_type in {"daily_overview", "fixed_slot"} else None
    rule.target_start_time = parse_time(target_start_time) if rule_type == "fixed_slot" else None
    rule.subject_id = subject_id if rule_type == "subject_before" else None
    rule.lead_minutes = lead_minutes if rule_type == "subject_before" else None
    validate_rule(rule)


@router.post("/settings/automations")
async def add_automation(
    name: str = Form(...), rule_type: str = Form(...), weekdays: list[int] = Form(...),
    run_time: str = Form(""), target_start_time: str = Form(""), subject_id: str = Form(""),
    lead_minutes: int = Form(20), db: Session = Depends(get_db),
):
    rule = AutomationRule(name="", rule_type="daily_overview", weekdays="0")
    try:
        _apply_automation_form(rule, name=name, rule_type=rule_type, weekdays=weekdays, run_time=run_time, target_start_time=target_start_time, subject_id=subject_id, lead_minutes=lead_minutes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if rule.subject_id and not db.get(Subject, rule.subject_id):
        raise HTTPException(400, "Unbekanntes Fach")
    db.add(rule)
    db.commit()
    return RedirectResponse("/settings/automations", status_code=303)


@router.post("/settings/automations/{rule_id}")
async def update_automation(
    rule_id: int, name: str = Form(...), rule_type: str = Form(...), weekdays: list[int] = Form(...),
    run_time: str = Form(""), target_start_time: str = Form(""), subject_id: str = Form(""),
    lead_minutes: int = Form(20), db: Session = Depends(get_db),
):
    rule = db.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(404)
    try:
        _apply_automation_form(rule, name=name, rule_type=rule_type, weekdays=weekdays, run_time=run_time, target_start_time=target_start_time, subject_id=subject_id, lead_minutes=lead_minutes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if rule.subject_id and not db.get(Subject, rule.subject_id):
        raise HTTPException(400, "Unbekanntes Fach")
    db.commit()
    return RedirectResponse("/settings/automations", status_code=303)


@router.post("/settings/automations/{rule_id}/toggle")
async def toggle_automation(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(404)
    rule.enabled = not rule.enabled
    db.commit()
    return RedirectResponse("/settings/automations", status_code=303)


@router.post("/settings/automations/{rule_id}/delete")
async def delete_automation(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(AutomationRule, rule_id)
    if rule:
        db.delete(rule)
        db.commit()
    return RedirectResponse("/settings/automations", status_code=303)


@router.post("/settings/automations/recommended/create")
async def recommended_automations(db: Session = Depends(get_db)):
    create_recommended_rules(db)
    return RedirectResponse("/settings/automations", status_code=303)


@router.post("/settings/exceptions")
async def add_exception(
    exception_date: date = Form(...), kind: str = Form(...), schedule_entry_id: str = Form(""),
    subject_id: str = Form(""), start_time: str = Form(""), end_time: str = Form(""), note: str = Form(""), db: Session = Depends(get_db),
):
    if kind not in {"cancel", "move", "add", "global_disabled_date"}:
        raise HTTPException(400)
    parse_time = lambda raw: time.fromisoformat(raw) if raw else None
    db.add(ScheduleException(exception_date=exception_date, kind=kind, schedule_entry_id=int(schedule_entry_id) if schedule_entry_id else None, subject_id=subject_id or None, start_time=parse_time(start_time), end_time=parse_time(end_time), note=note[:300]))
    db.commit()
    return RedirectResponse("/settings/exceptions", status_code=303)


@router.get("/settings/integrations")
async def settings_integrations(request: Request, db: Session = Depends(get_db)):
    from app.config import get_settings
    settings = get_settings()
    last_tick = db.get(Setting, "last_tick")
    connection = active_connection(db)
    statuses = {
        "iserv": bool(settings.iserv_webdav_url and settings.iserv_username and settings.iserv_password),
        "provider": provider_mode(db, settings),
        "plugin": True,
        "openai": bool(settings.openai_api_key),
        "push": bool(settings.vapid_public_key and settings.vapid_private_key),
        "pebbule": bool(settings.pebbule_api_url and settings.pebbule_integration_secret),
        "scheduler": last_tick.value if last_tick else None,
    }
    return render(request, "settings/integrations.html", {"statuses": statuses, "chatgpt_connection": connection, "pairing_command": None})


@router.post("/settings/integrations/chatgpt/pair")
async def settings_chatgpt_pair(request: Request, db: Session = Depends(get_db)):
    from app.config import get_settings
    settings = get_settings()
    _pairing, token, _client_id = create_pairing(db)
    connection = active_connection(db)
    last_tick = db.get(Setting, "last_tick")
    statuses = {
        "iserv": bool(settings.iserv_webdav_url and settings.iserv_username and settings.iserv_password),
        "provider": provider_mode(db, settings),
        "plugin": True,
        "openai": bool(settings.openai_api_key),
        "push": bool(settings.vapid_public_key and settings.vapid_private_key),
        "pebbule": bool(settings.pebbule_api_url and settings.pebbule_integration_secret),
        "scheduler": last_tick.value if last_tick else None,
    }
    command = f"python3 ~/Downloads/classbrief_connect.py --classbrief {settings.app_base_url.rstrip('/')} --pair-token {token}"
    return render(
        request,
        "settings/integrations.html",
        {"statuses": statuses, "chatgpt_connection": connection, "pairing_command": command},
    )


@router.get("/settings/integrations/chatgpt/helper")
async def settings_chatgpt_helper():
    path = Path(__file__).resolve().parents[2] / "scripts" / "connect_chatgpt.py"
    return FileResponse(path, filename="classbrief_connect.py", media_type="text/x-python")


@router.post("/settings/integrations/chatgpt/disconnect")
async def settings_chatgpt_disconnect(db: Session = Depends(get_db)):
    from app.config import get_settings
    connection = active_connection(db)
    if connection is not None:
        await disconnect(db, get_settings(), connection)
    return RedirectResponse("/settings/integrations", status_code=303)


@router.get("/settings/usage")
async def settings_usage(request: Request, db: Session = Depends(get_db)):
    from app.config import get_settings
    rows = db.execute(select(AIUsage.purpose, func.count(AIUsage.id), func.sum(AIUsage.input_tokens), func.sum(AIUsage.output_tokens), func.sum(AIUsage.estimated_cost)).where(AIUsage.timestamp >= month_start()).group_by(AIUsage.purpose)).all()
    settings = get_settings()
    return render(request, "settings/usage.html", {"rows": rows, "spend": current_spend(db), "budget": settings.monthly_ai_budget_usd, "provider": provider_mode(db, settings)})
