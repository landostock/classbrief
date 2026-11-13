from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Lesson, ScheduleEntry, ScheduleException, Subject
from app.services.pebbule import PebbuleClient, PebbuleError, configured_subject_map, normalize_subject

BERLIN = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True)
class ResolvedLesson:
    subject: Subject
    start: datetime
    end: datetime
    source: str = "schedule"
    room: str | None = None
    teacher: str | None = None
    change_kind: str | None = None
    note: str | None = None


def _at(day: date, value: time) -> datetime:
    return datetime.combine(day, value, tzinfo=BERLIN)


def as_berlin(value: datetime) -> datetime:
    """Restore the configured zone when SQLite returns a naive DateTime."""
    return value if value.tzinfo is not None else value.replace(tzinfo=BERLIN)


def resolve_day(db: Session, day: date) -> list[ResolvedLesson]:
    exceptions = list(db.scalars(select(ScheduleException).where(ScheduleException.exception_date == day)))
    if any(item.kind == "global_disabled_date" for item in exceptions):
        return []

    entries = list(db.scalars(select(ScheduleEntry).where(ScheduleEntry.weekday == day.weekday(), ScheduleEntry.enabled.is_(True))))
    by_id = {entry.id: entry for entry in entries}
    cancelled = {item.schedule_entry_id for item in exceptions if item.kind in {"cancel", "move"}}
    result = [
        ResolvedLesson(entry.subject, _at(day, entry.start_time), _at(day, entry.end_time))
        for entry in entries if entry.id not in cancelled and entry.subject.enabled
    ]

    for item in exceptions:
        if item.kind == "move" and item.schedule_entry_id in by_id:
            entry = by_id[item.schedule_entry_id]
            start = item.start_time or entry.start_time
            end = item.end_time or entry.end_time
            result.append(ResolvedLesson(entry.subject, _at(day, start), _at(day, end), "move"))
        elif item.kind == "add" and item.subject and item.start_time and item.end_time and item.subject.enabled:
            result.append(ResolvedLesson(item.subject, _at(day, item.start_time), _at(day, item.end_time), "add"))
    return sorted(result, key=lambda item: item.start)


def _pebbule_subjects(db: Session) -> dict[str, Subject]:
    settings = get_settings()
    subjects = list(db.scalars(select(Subject).where(Subject.enabled.is_(True))))
    lookup: dict[str, Subject] = {}
    by_id = {item.id: item for item in subjects}
    by_name = {normalize_subject(item.name): item for item in subjects}
    for item in subjects:
        lookup[normalize_subject(item.name)] = item
        lookup[normalize_subject(item.short_name)] = item
        lookup[normalize_subject(item.id)] = item
    synonyms = {"geographie": "geografie", "erdkunde": "geografie", "mathematik": "mathe", "deu": "deutsch", "eng": "englisch", "lat": "latein", "bio": "biologie", "ges": "geschichte", "rel": "religion"}
    for external, local in synonyms.items():
        subject = lookup.get(normalize_subject(local))
        if subject:
            lookup[normalize_subject(external)] = subject
    for external, target in configured_subject_map(settings).items():
        subject = by_id.get(target) or by_name.get(normalize_subject(target))
        if subject:
            lookup[external] = subject
    return lookup


def sync_pebbule_range(db: Session, start_day: date, end_day: date) -> bool:
    settings = get_settings()
    if not settings.pebbule_api_url or not settings.pebbule_integration_secret:
        return False
    try:
        external = PebbuleClient(settings).fetch_schedule(start_day, end_day)
    except PebbuleError:
        return False
    lookup = _pebbule_subjects(db)
    range_start = _at(start_day, time.min)
    range_end = _at(end_day + timedelta(days=1), time.min)
    existing = list(db.scalars(select(Lesson).where(Lesson.scheduled_start >= range_start, Lesson.scheduled_start < range_end)))
    for lesson in existing:
        if lesson.subject_id in {subject.id for subject in lookup.values()}:
            lesson.status = "cancelled"
    for item in external:
        subject = lookup.get(normalize_subject(item.subject))
        if not subject:
            continue
        start_at = item.start.astimezone(BERLIN)
        end_at = item.end.astimezone(BERLIN)
        lesson = db.scalar(select(Lesson).where(Lesson.subject_id == subject.id, Lesson.scheduled_start == start_at))
        if lesson is None:
            lesson = Lesson(subject_id=subject.id, scheduled_start=start_at, scheduled_end=end_at, source="pebbule")
            db.add(lesson)
        lesson.scheduled_end = end_at
        lesson.source = "pebbule"
        lesson.status = "cancelled" if item.status == "cancelled" else ("changed" if item.status == "changed" else "scheduled")
        lesson.room = item.room
        lesson.teacher = item.teacher
        lesson.change_kind = item.kind
        lesson.note = item.note
    db.commit()
    return True


def _stored_day(db: Session, day: date) -> list[Lesson]:
    start = _at(day, time.min)
    end = _at(day + timedelta(days=1), time.min)
    return list(db.scalars(select(Lesson).where(Lesson.scheduled_start >= start, Lesson.scheduled_start < end, Lesson.status != "cancelled").order_by(Lesson.scheduled_start)))


def ensure_lessons(db: Session, day: date) -> list[Lesson]:
    if sync_pebbule_range(db, day, day):
        return _stored_day(db, day)
    lessons: list[Lesson] = []
    for item in resolve_day(db, day):
        lesson = db.scalar(select(Lesson).where(Lesson.subject_id == item.subject.id, Lesson.scheduled_start == item.start))
        if lesson is None:
            lesson = Lesson(subject_id=item.subject.id, scheduled_start=item.start, scheduled_end=item.end, source=item.source, room=item.room, teacher=item.teacher, change_kind=item.change_kind, note=item.note)
            db.add(lesson)
            db.flush()
        lesson.status = "scheduled"
        lessons.append(lesson)
    db.commit()
    return lessons


def upcoming_lessons(db: Session, now: datetime, days: int = 7, limit: int = 8) -> list[Lesson]:
    start_day = now.astimezone(BERLIN).date()
    if not sync_pebbule_range(db, start_day, start_day + timedelta(days=days)):
        for offset in range(days + 1):
            ensure_lessons(db, start_day + timedelta(days=offset))
    return list(db.scalars(select(Lesson).where(Lesson.scheduled_start >= now, Lesson.status != "cancelled").order_by(Lesson.scheduled_start).limit(limit)))


def lessons_for_range(db: Session, start_day: date, end_day: date) -> list[Lesson]:
    """Resolve one authoritative plan for a visible date range."""
    if end_day < start_day:
        return []
    if not sync_pebbule_range(db, start_day, end_day):
        day = start_day
        while day <= end_day:
            ensure_lessons(db, day)
            day += timedelta(days=1)
    start = _at(start_day, time.min)
    end = _at(end_day + timedelta(days=1), time.min)
    return list(
        db.scalars(
            select(Lesson)
            .where(Lesson.scheduled_start >= start, Lesson.scheduled_start < end, Lesson.status != "cancelled")
            .order_by(Lesson.scheduled_start)
        )
    )


def due_lessons(db: Session, now: datetime) -> list[Lesson]:
    lessons = ensure_lessons(db, now.astimezone(BERLIN).date())
    due: list[Lesson] = []
    for lesson in lessons:
        if not lesson.subject.briefing_enabled:
            continue
        scheduled_start = as_berlin(lesson.scheduled_start)
        preparation_at = scheduled_start - timedelta(minutes=lesson.subject.preparation_minutes)
        if preparation_at <= now.astimezone(BERLIN) < scheduled_start:
            due.append(lesson)
    return due


def previous_lesson(db: Session, subject_id: str, before: datetime) -> Lesson | None:
    return db.scalar(
        select(Lesson)
        .where(Lesson.subject_id == subject_id, Lesson.scheduled_end < before, Lesson.status != "cancelled")
        .order_by(Lesson.scheduled_end.desc())
        .limit(1)
    )
