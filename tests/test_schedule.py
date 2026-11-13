from dataclasses import replace
from datetime import date, datetime, time, timezone

from app.models import ScheduleEntry, ScheduleException, Subject
from app.config import get_settings
from app.services.pebbule import PebbuleLesson
from app.services.schedule import BERLIN, due_lessons, resolve_day, sync_pebbule_range


def seed(db):
    subject = Subject(id="geschichte", name="Geschichte", short_name="Ges", webdav_path="/Groups/Geschichte/")
    db.add(subject)
    db.flush()
    entry = ScheduleEntry(subject_id=subject.id, weekday=0, start_time=time(8), end_time=time(9, 30))
    db.add(entry)
    db.commit()
    return subject, entry


def test_double_lesson_stays_one_block(db):
    seed(db)
    lessons = resolve_day(db, date(2026, 9, 21))
    assert len(lessons) == 1
    assert lessons[0].end.hour == 9 and lessons[0].end.minute == 30


def test_cancelled_lesson_is_removed(db):
    _, entry = seed(db)
    db.add(ScheduleException(exception_date=date(2026, 9, 21), kind="cancel", schedule_entry_id=entry.id))
    db.commit()
    assert resolve_day(db, date(2026, 9, 21)) == []


def test_moved_lesson_replaces_original(db):
    _, entry = seed(db)
    db.add(ScheduleException(exception_date=date(2026, 9, 21), kind="move", schedule_entry_id=entry.id, start_time=time(11, 40), end_time=time(13, 10)))
    db.commit()
    lessons = resolve_day(db, date(2026, 9, 21))
    assert len(lessons) == 1
    assert lessons[0].start.hour == 11 and lessons[0].source == "move"


def test_global_disabled_date_removes_all(db):
    seed(db)
    db.add(ScheduleException(exception_date=date(2026, 9, 21), kind="global_disabled_date"))
    db.commit()
    assert resolve_day(db, date(2026, 9, 21)) == []


def test_due_lessons_accepts_naive_datetimes_loaded_from_sqlite(db):
    subject, _ = seed(db)
    subject.preparation_minutes = 20
    db.commit()

    due = due_lessons(db, datetime(2026, 9, 21, 7, 45, tzinfo=BERLIN))

    assert len(due) == 1
    assert due[0].subject_id == "geschichte"


def test_due_lessons_skips_subjects_without_briefings(db):
    subject, _ = seed(db)
    subject.preparation_minutes = 20
    subject.briefing_enabled = False
    db.commit()

    due = due_lessons(db, datetime(2026, 9, 21, 7, 45, tzinfo=BERLIN))

    assert due == []


def test_pebbule_schedule_overrides_manual_slot_and_keeps_change_details(monkeypatch, db):
    subject, _entry = seed(db)
    manual = resolve_day(db, date(2026, 9, 21))[0]
    from app.models import Lesson
    old = Lesson(subject_id=subject.id, scheduled_start=manual.start, scheduled_end=manual.end, source="schedule")
    db.add(old)
    db.commit()
    settings = replace(get_settings(), pebbule_api_url="https://pebbule.example", pebbule_integration_secret="secret")
    monkeypatch.setattr("app.services.schedule.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.schedule.PebbuleClient.fetch_schedule",
        lambda *_args: [PebbuleLesson(
            subject="Geschichte",
            start=datetime(2026, 9, 21, 9, 20, tzinfo=timezone.utc),
            end=datetime(2026, 9, 21, 10, 5, tzinfo=timezone.utc),
            status="changed",
            kind="changed",
            room="2.5",
            teacher="Kb",
            note="Vertretung",
        )],
    )

    assert sync_pebbule_range(db, date(2026, 9, 21), date(2026, 9, 21)) is True
    db.refresh(old)
    assert old.status == "cancelled"
    current = db.query(Lesson).filter(Lesson.source == "pebbule").one()
    assert current.status == "changed"
    assert current.room == "2.5"
    assert current.teacher == "Kb"
