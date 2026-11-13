from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Lesson, Setting
from app.services.automation import build_automation_plan, complete_automation_run, resolve_automation_run
from app.services.chatgpt_plan import provider_mode
from app.services.lesson_preparation import AlreadyProcessing, prepare_lesson
from app.services.notifications import send_briefing_push
from app.services.schedule import BERLIN, due_lessons

logger = logging.getLogger("classbrief.scheduler")


async def run_due_briefings(db: Session, settings: Settings) -> dict:
    now = datetime.now(BERLIN)
    mode = provider_mode(db, settings)
    if mode == "plugin":
        return {
            "checked_at": now.isoformat(),
            "mode": "plugin",
            "due_lessons": 0,
            "generated": 0,
            "cached": 0,
            "failed": 0,
            "message": "Direkte KI ist noch nicht verbunden.",
        }
    plan = build_automation_plan(db)
    now_minutes = now.hour * 60 + now.minute
    due_tasks = []
    for task in plan["tasks"]:
        tolerance = int(task["selector"].get("late_tolerance_minutes", 9))
        for slot in task["selector"].get("slots", []):
            hour, minute = (int(part) for part in slot["trigger_time"].split(":")[:2])
            delta = now_minutes - (hour * 60 + minute)
            if slot["trigger_weekday"] == now.weekday() and 0 <= delta <= tolerance:
                marker = f"direct_dispatch:{task['automation_key']}:{now.date().isoformat()}:{hour:02d}{minute:02d}"
                if db.get(Setting, marker) is None:
                    due_tasks.append((task, marker))
                break

    due = [] if plan["tasks"] else due_lessons(db, now)
    generated = cached = failed = 0
    for task, marker in due_tasks:
        resolved = resolve_automation_run(db, task["automation_key"], now=now)
        run_generated = 0
        try:
            for target in resolved["targets"]:
                lesson = db.get(Lesson, target["lesson_id"])
                if lesson is None or target["briefing_status"] == "ready":
                    continue
                try:
                    status, briefing = await prepare_lesson(db, lesson, settings, trigger="automatic")
                    generated += status == "updated"
                    cached += status == "cached"
                    run_generated += status in {"updated", "cached"}
                    if status == "updated":
                        await send_briefing_push(db, briefing, settings)
                except AlreadyProcessing:
                    cached += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("automatic_briefing_failed", extra={"lesson_id": lesson.id, "error": type(exc).__name__})
            if resolved["status"] == "ready":
                complete_automation_run(db, resolved["run_id"], "completed", run_generated)
        except Exception as exc:
            complete_automation_run(db, resolved["run_id"], "failed", run_generated, type(exc).__name__)
            failed += 1
        finally:
            if db.get(Setting, marker) is None:
                db.add(Setting(key=marker, value=now.isoformat()))
                db.commit()
    for lesson in due:
        try:
            status, briefing = await prepare_lesson(db, lesson, settings, trigger="automatic")
            generated += status == "updated"
            cached += status == "cached"
            if status == "updated":
                await send_briefing_push(db, briefing, settings)
        except AlreadyProcessing:
            cached += 1
        except Exception as exc:
            failed += 1
            logger.warning("automatic_briefing_failed", extra={"lesson_id": lesson.id, "error": type(exc).__name__})
    stamp = now.isoformat()
    last_tick = db.get(Setting, "last_tick")
    if last_tick:
        last_tick.value = stamp
        last_tick.updated_at = datetime.now(timezone.utc)
    else:
        db.add(Setting(key="last_tick", value=stamp))
    db.commit()
    return {"checked_at": stamp, "mode": mode, "due_lessons": len(due), "automation_tasks": len(due_tasks), "generated": generated, "cached": cached, "failed": failed}
