from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models import Briefing, BriefingClarification, Lesson, Subject


def _pending_question(db):
    subject = Subject(id="bio", name="Biologie", short_name="Bio", webdav_path="/bio/")
    lesson = Lesson(
        subject=subject,
        subject_id="bio",
        scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        scheduled_end=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db.add_all([subject, lesson])
    db.flush()
    clarification = BriefingClarification(
        lesson_id=lesson.id,
        subject_id="bio",
        questions_json=json.dumps([
            {
                "id": "progress",
                "prompt": "Wie weit seid ihr bei der Zellteilung gekommen?",
                "why_it_matters": "So wird alter und neuer Stoff sauber getrennt.",
                "options": [
                    {"id": "started", "label": "Wir haben gerade begonnen"},
                    {"id": "done", "label": "Mitose ist bereits abgeschlossen"},
                    {"id": "unknown", "label": "Weiß ich nicht – vorsichtig ableiten"},
                ],
            }
        ], ensure_ascii=False),
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
    )
    db.add(clarification)
    db.commit()
    return clarification


def test_pending_clarification_is_restored(auth_client, db):
    clarification = _pending_question(db)

    response = auth_client.get("/api/clarifications/pending")

    assert response.status_code == 200
    assert response.json()["clarification"]["id"] == clarification.id
    assert response.json()["clarification"]["subject"] == "Biologie"
    assert response.json()["clarification"]["questions"][0]["id"] == "progress"


def test_clarification_answer_is_used_before_briefing(auth_client, db, monkeypatch):
    clarification = _pending_question(db)
    captured = {}

    async def fake_prepare(session, lesson, _settings, **kwargs):
        captured["answers"] = kwargs["clarification_answers"]
        briefing = Briefing(
            lesson_id=lesson.id,
            subject_id=lesson.subject_id,
            material_state_hash="answer-specific-state",
            probable_topic="Mitose",
            quick_brief="Kurz",
            medium_brief="Mittel",
            deep_brief_json="{}",
        )
        session.add(briefing)
        session.commit()
        session.refresh(briefing)
        return "updated", briefing

    monkeypatch.setattr("app.routes.api.prepare_lesson", fake_prepare)
    response = auth_client.post(
        f"/api/clarifications/{clarification.id}/answer",
        json={"answers": {"progress": "done"}},
    )

    assert response.status_code == 200
    assert response.json()["briefing_id"]
    assert captured["answers"] == [{
        "question": "Wie weit seid ihr bei der Zellteilung gekommen?",
        "answer": "Mitose ist bereits abgeschlossen",
        "certainty": "user_confirmed",
    }]
    db.expire_all()
    assert db.get(BriefingClarification, clarification.id).status == "completed"
