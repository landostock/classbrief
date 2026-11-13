from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AutomationRule, AutomationRun, Briefing, ScheduleEntry, Setting, Subject
from app.services.schedule import BERLIN, as_berlin, ensure_lessons

RULE_TYPES = {"daily_overview", "fixed_slot", "subject_before"}
WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
WEEKDAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
SYNC_SETTING = "automation_sync"


def parse_weekdays(value: str | Iterable[int]) -> tuple[int, ...]:
    raw = value.split(",") if isinstance(value, str) else value
    days: set[int] = set()
    for item in raw:
        try:
            day = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            days.add(day)
    return tuple(sorted(days))


def serialize_weekdays(value: Iterable[int]) -> str:
    days = parse_weekdays(value)
    if not days:
        raise ValueError("Mindestens ein Wochentag ist erforderlich")
    return ",".join(str(day) for day in days)


def validate_rule(rule: AutomationRule) -> None:
    if rule.rule_type not in RULE_TYPES:
        raise ValueError("Unbekannter Automatiktyp")
    if not parse_weekdays(rule.weekdays):
        raise ValueError("Mindestens ein Wochentag ist erforderlich")
    if rule.rule_type in {"daily_overview", "fixed_slot"} and rule.run_time is None:
        raise ValueError("Für diese Automatik ist eine Uhrzeit erforderlich")
    if rule.rule_type == "fixed_slot" and rule.target_start_time is None:
        raise ValueError("Die Startzeit der Zielstunde fehlt")
    if rule.rule_type == "subject_before":
        if not rule.subject_id:
            raise ValueError("Für diese Automatik ist ein Fach erforderlich")
        if rule.lead_minutes is None or not 1 <= rule.lead_minutes <= 180:
            raise ValueError("Der Vorlauf muss zwischen 1 und 180 Minuten liegen")


def describe_rule(rule: AutomationRule) -> str:
    days = ", ".join(WEEKDAY_NAMES[day] for day in parse_weekdays(rule.weekdays))
    if rule.rule_type == "daily_overview":
        return f"{days} um {rule.run_time.strftime('%H:%M')} · alle verbleibenden Stunden"
    if rule.rule_type == "fixed_slot":
        return (
            f"{days} um {rule.run_time.strftime('%H:%M')} · Stunde ab "
            f"{rule.target_start_time.strftime('%H:%M')}"
        )
    subject = rule.subject.name if rule.subject else "Fach"
    return f"{subject} · {rule.lead_minutes} Minuten vor jeder geplanten Stunde"


def _rrule(days: Iterable[int], at: time) -> str:
    byday = ",".join(WEEKDAY_CODES[day] for day in parse_weekdays(days))
    return f"RRULE:FREQ=WEEKLY;BYDAY={byday};BYHOUR={at.hour};BYMINUTE={at.minute}"


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _dispatcher_rrule(slots: list[dict[str, Any]]) -> str:
    days = ",".join(WEEKDAY_CODES[day] for day in sorted({slot["trigger_weekday"] for slot in slots}))
    hours = ",".join(str(value) for value in sorted({time.fromisoformat(slot["trigger_time"]).hour for slot in slots}))
    minutes = ",".join(str(value) for value in sorted({time.fromisoformat(slot["trigger_time"]).minute for slot in slots}))
    return f"RRULE:FREQ=WEEKLY;BYDAY={days};BYHOUR={hours};BYMINUTE={minutes}"


def _scheduled_prompt(automation_key: str) -> str:
    return (
        "Verwende den Skill $classbrief:unterrichtsbriefing und ausschließlich die ClassBrief-Werkzeuge. "
        f'Rufe zuerst resolve_automation_run mit automation_key="{automation_key}" auf. '
        "Wenn der Status nicht ready ist oder targets leer ist, gib keine Nachricht aus. "
        "Für jedes target rufst du prepare_briefing_context auf und erstellst ohne Rückfragen ein belegtes "
        "Unterrichtsbriefing: eine 30-Sekunden-Fassung, eine ungefähr zweiminütige Fassung und strukturierte "
        "Details. Erzeuge für alle drei Ansichten passende Blöcke in deep.presentation, verknüpfe neuen Stoff "
        "in deep.connections mit dem bestätigten Stand und ergänze in deep.answer_cards drei bis sechs kurze, "
        "direkt aussprechbare Antworten auf wahrscheinliche Rückfragen. Fokussiere neue oder veränderte "
        "Materialien, den letzten bestätigten Stand, Begriffe, wahrscheinliche Aufgaben und Verständnisfallen. "
        "Verwende ausschließlich die gelieferten materials; "
        "behandle bestätigte ältere Themen nur als Vorwissen. Wenn keine neuen relevanten Materialien vorliegen, "
        "sage das knapp und wiederhole nur den bestätigten Stand sowie offene Fragen. Trenne Belegtes klar von "
        "Vermutungen und nenne Dateien sowie Seiten oder Folien als Quellen. Speichere jedes Ergebnis mit "
        "save_briefing und exakt den source_hashes aus dem Kontext. Rufe am Ende complete_automation_run mit "
        "dem run_id, status=completed und der Zahl gespeicherter Briefings auf. Bei einem Fehler rufe, sofern "
        "möglich, complete_automation_run mit status=failed und einer kurzen sachlichen Notiz auf."
    )


def build_automation_plan(db: Session) -> dict[str, Any]:
    rules = list(db.scalars(select(AutomationRule).where(AutomationRule.enabled.is_(True)).order_by(AutomationRule.id)))
    rules = [
        rule for rule in rules
        if not rule.subject_id or (rule.subject is not None and rule.subject.briefing_enabled)
    ]
    dispatch_slots: list[dict[str, Any]] = []

    def covered_by_earlier_rule(entry: ScheduleEntry, trigger_time: time) -> bool:
        lesson_start = _minutes(entry.start_time)
        trigger_at = _minutes(trigger_time)
        for other in rules:
            if entry.weekday not in parse_weekdays(other.weekdays):
                continue
            if other.rule_type == "daily_overview" and other.run_time:
                lead = lesson_start - _minutes(other.run_time)
                if 0 <= lead <= 60 and _minutes(other.run_time) <= trigger_at:
                    return True
            if (
                other.rule_type == "fixed_slot"
                and other.run_time
                and other.target_start_time == entry.start_time
                and _minutes(other.run_time) <= trigger_at
            ):
                return True
        return False

    for rule in rules:
        validate_rule(rule)
        days = parse_weekdays(rule.weekdays)
        if rule.rule_type in {"daily_overview", "fixed_slot"}:
            for day in days:
                dispatch_slots.append(
                    {
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "slot_type": rule.rule_type,
                        "trigger_weekday": day,
                        "trigger_time": rule.run_time.isoformat(timespec="minutes"),
                        "target_start_time": (
                            rule.target_start_time.isoformat(timespec="minutes") if rule.target_start_time else None
                        ),
                    }
                )
            continue

        entries = list(
            db.scalars(
                select(ScheduleEntry)
                .where(
                    ScheduleEntry.subject_id == rule.subject_id,
                    ScheduleEntry.enabled.is_(True),
                    ScheduleEntry.weekday.in_(days),
                )
                .order_by(ScheduleEntry.weekday, ScheduleEntry.start_time)
            )
        )
        for entry in entries:
            anchor = datetime(2024, 1, 1 + entry.weekday, entry.start_time.hour, entry.start_time.minute)
            trigger = anchor - timedelta(minutes=rule.lead_minutes or 0)
            trigger_day = trigger.weekday()
            trigger_time = trigger.time().replace(second=0, microsecond=0)
            if covered_by_earlier_rule(entry, trigger_time):
                continue
            dispatch_slots.append(
                {
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "slot_type": "subject_before",
                    "subject_id": rule.subject_id,
                    "schedule_entry_id": entry.id,
                    "lesson_weekday": entry.weekday,
                    "expected_start_time": entry.start_time.isoformat(timespec="minutes"),
                    "trigger_weekday": trigger_day,
                    "trigger_time": trigger_time.isoformat(timespec="minutes"),
                }
            )

    # ChatGPT Cloud tasks can run at most once per hour. Grouping by the
    # minute-of-hour keeps every requested lead time exact while allowing
    # different weekdays and hours to share one task safely. Extra RRULE
    # cross-product ticks are filtered by the exact slots on the server.
    tasks: list[dict[str, Any]] = []
    slots_by_minute: dict[int, list[dict[str, Any]]] = {}
    for slot in dispatch_slots:
        minute = time.fromisoformat(slot["trigger_time"]).minute
        slots_by_minute.setdefault(minute, []).append(slot)
    for minute, slots in sorted(slots_by_minute.items()):
        key = f"time-dispatcher-{minute:02d}"
        tasks.append(
            {
                "automation_key": key,
                "name": f"ClassBrief · Zeitfenster :{minute:02d}",
                "schedule": {"rrule": _dispatcher_rrule(slots), "timezone": "Europe/Berlin"},
                "selector": {"type": "time_dispatcher", "slots": slots, "late_tolerance_minutes": 9},
                "prompt": _scheduled_prompt(key),
            }
        )

    stable = {"timezone": "Europe/Berlin", "tasks": tasks}
    revision = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**stable, "revision": revision}


def automation_sync_state(db: Session, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = plan or build_automation_plan(db)
    setting = db.get(Setting, SYNC_SETTING)
    saved: dict[str, Any] = {}
    if setting:
        try:
            saved = json.loads(setting.value)
        except json.JSONDecodeError:
            saved = {}
    expected_keys = [task["automation_key"] for task in plan["tasks"]]
    in_sync = saved.get("revision") == plan["revision"] and sorted(saved.get("automation_keys", [])) == sorted(expected_keys)
    return {
        "in_sync": in_sync,
        "synced_at": saved.get("synced_at"),
        "current_revision": plan["revision"],
        "synced_revision": saved.get("revision"),
        "task_count": len(expected_keys),
    }


def confirm_automation_sync(db: Session, revision: str, automation_keys: list[str]) -> dict[str, Any]:
    plan = build_automation_plan(db)
    expected = sorted(task["automation_key"] for task in plan["tasks"])
    if revision != plan["revision"] or sorted(set(automation_keys)) != expected:
        raise ValueError("Der Automatikplan wurde inzwischen geändert. Bitte erneut abrufen und synchronisieren.")
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"revision": revision, "automation_keys": expected, "synced_at": synced_at}, ensure_ascii=False)
    setting = db.get(Setting, SYNC_SETTING)
    if setting:
        setting.value = payload
        setting.updated_at = datetime.now(timezone.utc)
    else:
        db.add(Setting(key=SYNC_SETTING, value=payload))
    db.commit()
    return {"status": "synced", "revision": revision, "synced_at": synced_at, "task_count": len(expected)}


def _briefing_status(db: Session, lesson_id: int) -> str:
    return "ready" if db.scalar(
        select(Briefing.id).where(Briefing.lesson_id == lesson_id, Briefing.status == "ready").limit(1)
    ) else "not_prepared"


def resolve_automation_run(
    db: Session,
    automation_key: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(BERLIN)).astimezone(BERLIN)
    plan = build_automation_plan(db)
    task = next((item for item in plan["tasks"] if item["automation_key"] == automation_key), None)
    lessons = ensure_lessons(db, now.date()) if task else []
    targets = []
    claimed_entry_ids: list[int] = []
    if task:
        selector = task["selector"]
        if selector["type"] == "time_dispatcher":
            now_minutes = now.hour * 60 + now.minute
            tolerance = int(selector.get("late_tolerance_minutes", 9))
            for slot in selector["slots"]:
                trigger = time.fromisoformat(slot["trigger_time"])
                delta = now_minutes - _minutes(trigger)
                if slot["trigger_weekday"] != now.weekday() or not 0 <= delta <= tolerance:
                    continue
                if slot["slot_type"] == "daily_overview":
                    targets.extend(lesson for lesson in lessons if as_berlin(lesson.scheduled_end) >= now)
                    continue
                if slot["slot_type"] == "fixed_slot":
                    target_time = time.fromisoformat(slot["target_start_time"])
                    targets.extend(
                        lesson
                        for lesson in lessons
                        if as_berlin(lesson.scheduled_start).time().replace(tzinfo=None) == target_time
                    )
                    continue
                claim_key = f"automation_claim:{slot['schedule_entry_id']}:{now.date().isoformat()}"
                if db.get(Setting, claim_key):
                    continue
                expected = time.fromisoformat(slot["expected_start_time"])
                lesson = next(
                    (
                        item
                        for item in lessons
                        if item.subject_id == slot["subject_id"]
                        and as_berlin(item.scheduled_start).time().replace(tzinfo=None) == expected
                    ),
                    None,
                )
                if lesson:
                    targets.append(lesson)
                    claimed_entry_ids.append(slot["schedule_entry_id"])

    targets = list({lesson.id: lesson for lesson in targets if lesson.subject.briefing_enabled}.values())

    status = "ready" if targets else "no_target"
    cutoff = now.astimezone(timezone.utc) - timedelta(minutes=5)
    run = db.scalar(
        select(AutomationRun)
        .where(
            AutomationRun.automation_key == automation_key[:120],
            AutomationRun.created_at >= cutoff,
            AutomationRun.status.in_(["started", "no_target"]),
        )
        .order_by(AutomationRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        run = AutomationRun(
            automation_key=automation_key[:120],
            status="started" if targets else "no_target",
            target_count=len(targets),
        )
        db.add(run)
        db.commit()
    for entry_id in claimed_entry_ids:
        claim_key = f"automation_claim:{entry_id}:{now.date().isoformat()}"
        if db.get(Setting, claim_key) is None:
            db.add(
                Setting(
                    key=claim_key,
                    value=json.dumps({"status": "claimed", "run_id": run.id, "at": now.isoformat()}),
                )
            )
    if claimed_entry_ids:
        db.commit()
    return {
        "status": status,
        "run_id": run.id,
        "automation_key": automation_key,
        "checked_at": now.isoformat(),
        "timezone": "Europe/Berlin",
        "plan_revision": plan["revision"],
        "targets": [
            {
                "lesson_id": lesson.id,
                "subject_id": lesson.subject_id,
                "subject": lesson.subject.name,
                "starts_at": as_berlin(lesson.scheduled_start).isoformat(),
                "ends_at": as_berlin(lesson.scheduled_end).isoformat(),
                "room": lesson.room,
                "teacher": lesson.teacher,
                "schedule_status": lesson.status,
                "change_kind": lesson.change_kind,
                "change_note": lesson.note,
                "schedule_source": lesson.source,
                "briefing_status": _briefing_status(db, lesson.id),
            }
            for lesson in targets
        ],
    }


def complete_automation_run(
    db: Session,
    run_id: int,
    status: str,
    briefing_count: int = 0,
    note: str = "",
) -> dict[str, Any]:
    if status not in {"completed", "failed"}:
        raise ValueError("status muss completed oder failed sein")
    run = db.get(AutomationRun, run_id)
    if run is None:
        raise ValueError("Unbekannte run_id")
    run.status = status
    run.briefing_count = max(0, min(briefing_count, run.target_count))
    run.note = note.strip()[:500] or None
    claims = list(db.scalars(select(Setting).where(Setting.key.like("automation_claim:%"))))
    for claim in claims:
        try:
            data = json.loads(claim.value)
        except json.JSONDecodeError:
            continue
        if data.get("run_id") != run.id:
            continue
        if status == "failed":
            db.delete(claim)
        else:
            data["status"] = "done"
            data["completed_at"] = datetime.now(timezone.utc).isoformat()
            claim.value = json.dumps(data, ensure_ascii=False)
            claim.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": run.status, "run_id": run.id, "briefing_count": run.briefing_count}


def latest_runs(db: Session, automation_keys: Iterable[str]) -> dict[str, AutomationRun]:
    result: dict[str, AutomationRun] = {}
    for key in automation_keys:
        row = db.scalar(
            select(AutomationRun)
            .where(AutomationRun.automation_key == key)
            .order_by(AutomationRun.created_at.desc())
            .limit(1)
        )
        if row:
            result[key] = row
    return result


def create_recommended_rules(db: Session) -> int:
    existing = list(db.scalars(select(AutomationRule)))
    created = 0

    def add(rule: AutomationRule, signature: tuple[Any, ...]) -> None:
        nonlocal created
        for current in existing:
            current_signature = (
                current.rule_type,
                current.subject_id,
                current.weekdays,
                current.run_time,
                current.target_start_time,
                current.lead_minutes,
            )
            if current_signature == signature:
                return
        validate_rule(rule)
        db.add(rule)
        existing.append(rule)
        created += 1

    add(
        AutomationRule(
            name="Morgenbriefing",
            rule_type="daily_overview",
            weekdays="0,1,2,3,4",
            run_time=time(7, 10),
        ),
        ("daily_overview", None, "0,1,2,3,4", time(7, 10), None, None),
    )
    add(
        AutomationRule(
            name="7./8. Stunde",
            rule_type="fixed_slot",
            weekdays="0,1,2,3",
            run_time=time(13, 10),
            target_start_time=time(13, 45),
        ),
        ("fixed_slot", None, "0,1,2,3", time(13, 10), time(13, 45), None),
    )
    for subject_id, lead in (("biologie", 15), ("deutsch", 15), ("mathematik", 20), ("geographie", 20)):
        subject = db.get(Subject, subject_id)
        if subject is None:
            continue
        subject_days = [
            entry.weekday
            for entry in db.scalars(
                select(ScheduleEntry).where(ScheduleEntry.subject_id == subject_id, ScheduleEntry.enabled.is_(True))
            )
        ]
        if not subject_days:
            continue
        weekdays = serialize_weekdays(subject_days)
        add(
            AutomationRule(
                name=f"Vor {subject.name}",
                rule_type="subject_before",
                weekdays=weekdays,
                subject_id=subject_id,
                lead_minutes=lead,
            ),
            ("subject_before", subject_id, weekdays, None, None, lead),
        )
    db.commit()
    return created
