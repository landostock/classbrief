from datetime import date, datetime, time

import pytest

from app.models import AutomationRule, ScheduleEntry, ScheduleException, Subject
from app.services.automation import (
    automation_sync_state,
    build_automation_plan,
    complete_automation_run,
    confirm_automation_sync,
    resolve_automation_run,
)
from app.services.schedule import BERLIN


def _seed(db):
    math = Subject(id="mathematik", name="Mathematik", short_name="Mat", webdav_path="/mathe/")
    db.add(math)
    db.flush()
    entry = ScheduleEntry(subject_id=math.id, weekday=3, start_time=time(13, 45), end_time=time(15, 15))
    db.add(entry)
    db.flush()
    fixed = AutomationRule(
        name="7./8. Stunde",
        rule_type="fixed_slot",
        weekdays="0,1,2,3",
        run_time=time(13, 10),
        target_start_time=time(13, 45),
    )
    before = AutomationRule(
        name="Vor Mathematik",
        rule_type="subject_before",
        weekdays="3",
        subject_id=math.id,
        lead_minutes=15,
    )
    db.add_all([fixed, before])
    db.commit()
    return entry, fixed, before


def test_plan_expands_subject_rules_and_has_stable_revision(db):
    entry, fixed, before = _seed(db)
    fixed.enabled = False
    db.commit()

    first = build_automation_plan(db)
    second = build_automation_plan(db)

    assert first == second
    assert len(first["tasks"]) == 1
    subject_task = next(item for item in first["tasks"] if item["selector"]["type"] == "time_dispatcher")
    assert subject_task["automation_key"] == "time-dispatcher-30"
    assert subject_task["selector"]["slots"][0]["schedule_entry_id"] == entry.id
    assert "BYDAY=TH;BYHOUR=13;BYMINUTE=30" in subject_task["schedule"]["rrule"]
    assert "resolve_automation_run" in subject_task["prompt"]


def test_subject_dispatcher_claims_a_lesson_once(db):
    _entry, fixed, _before = _seed(db)
    fixed.enabled = False
    db.commit()
    now = datetime(2026, 11, 12, 13, 30, tzinfo=BERLIN)

    first = resolve_automation_run(db, "time-dispatcher-30", now=now)
    assert first["status"] == "ready"
    assert len(first["targets"]) == 1
    complete_automation_run(db, first["run_id"], "completed", 1)

    duplicate = resolve_automation_run(db, "time-dispatcher-30", now=now.replace(minute=35))
    assert duplicate["status"] == "no_target"
    assert duplicate["targets"] == []


def test_resolve_rechecks_day_exceptions_and_run_can_complete(db):
    entry, fixed, _before = _seed(db)
    now = datetime(2026, 11, 12, 13, 10, tzinfo=BERLIN)

    resolved = resolve_automation_run(db, "time-dispatcher-10", now=now)
    assert resolved["status"] == "ready"
    assert resolved["targets"][0]["subject_id"] == "mathematik"
    completed = complete_automation_run(db, resolved["run_id"], "completed", 1)
    assert completed["briefing_count"] == 1

    db.add(ScheduleException(exception_date=date(2026, 11, 12), kind="cancel", schedule_entry_id=entry.id))
    db.commit()
    later = resolve_automation_run(db, "time-dispatcher-10", now=now.replace(minute=20))
    assert later["status"] == "no_target"
    assert later["targets"] == []


def test_daily_overview_excludes_subjects_without_briefings(db):
    sport = Subject(
        id="sport", name="Sport", short_name="Spo", webdav_path="/sport/",
        briefing_enabled=False,
    )
    db.add(sport)
    db.flush()
    db.add(ScheduleEntry(subject_id="sport", weekday=3, start_time=time(13, 45), end_time=time(15, 15)))
    db.add(AutomationRule(
        name="Tagesübersicht", rule_type="daily_overview", weekdays="3", run_time=time(13, 10),
    ))
    db.commit()

    result = resolve_automation_run(
        db, "time-dispatcher-10", now=datetime(2026, 11, 12, 13, 10, tzinfo=BERLIN)
    )

    assert result["status"] == "no_target"
    assert result["targets"] == []


def test_sync_confirmation_rejects_stale_plan(db):
    _seed(db)
    plan = build_automation_plan(db)
    keys = [item["automation_key"] for item in plan["tasks"]]

    result = confirm_automation_sync(db, plan["revision"], keys)
    assert result["status"] == "synced"
    assert automation_sync_state(db)["in_sync"] is True

    db.add(AutomationRule(name="Morgen", rule_type="daily_overview", weekdays="0,1,2,3,4", run_time=time(7, 10)))
    db.commit()
    assert automation_sync_state(db)["in_sync"] is False
    with pytest.raises(ValueError):
        confirm_automation_sync(db, plan["revision"], keys)


def test_automation_settings_page_loads_with_rules(auth_client, db):
    _seed(db)
    response = auth_client.get("/settings/automations")
    assert response.status_code == 200
    assert "Cloud-Briefings" in response.text
    assert "7./8. Stunde" in response.text
    assert "Mit ChatGPT synchronisieren" in response.text
