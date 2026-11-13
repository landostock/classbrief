import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.schemas import MaterialAnalysisSchema
from app.config import get_settings
from app.models import Briefing, FileRecord, Lesson, Subject, SubjectMemory
from app.services.lesson_preparation import AlreadyProcessing, _batches, _unchanged, acquire_lock
from app.services.webdav import WebDAVItem


def test_etag_and_metadata_change_detection():
    record = FileRecord(subject_id="x", relative_path="/a.pdf", filename="a.pdf", etag='"1"', content_length=10)
    same = WebDAVItem("/a.pdf", "a.pdf", False, '"1"', None, 10, "application/pdf")
    changed = WebDAVItem("/a.pdf", "a.pdf", False, '"2"', None, 10, "application/pdf")
    assert _unchanged(record, same)
    assert not _unchanged(record, changed)


def test_adaptive_batches_respect_boundary():
    items = [(None, "a", "x" * 6), (None, "b", "x" * 4), (None, "c", "x" * 8)]
    result = _batches(items, 10)
    assert [len(batch) for batch in result] == [2, 1]


def test_processing_lock_blocks_same_subject(db):
    subject = Subject(id="ges", name="Geschichte", short_name="Ges", webdav_path="/ges/")
    db.add(subject)
    db.commit()
    settings = replace(get_settings(), processing_lock_timeout_minutes=10)
    acquire_lock(db, "ges", "manual", None, settings)
    with pytest.raises(AlreadyProcessing):
        acquire_lock(db, "ges", "manual", None, settings)


def test_material_schema_rejects_missing_required_data():
    with pytest.raises(Exception):
        MaterialAnalysisSchema.model_validate({"title": "Nur ein Titel"})


def test_feedback_updates_subject_memory(auth_client, db):
    subject = Subject(id="ges", name="Geschichte", short_name="Ges", webdav_path="/ges/")
    lesson = Lesson(subject_id="ges", scheduled_start=datetime.now(timezone.utc), scheduled_end=datetime.now(timezone.utc) + timedelta(minutes=45))
    db.add_all([subject, lesson])
    db.flush()
    briefing = Briefing(lesson_id=lesson.id, subject_id="ges", material_state_hash="x", probable_topic="Artikel 48", quick_brief="Kurz", medium_brief="Mittel", deep_brief_json=json.dumps({"summary": "Zusammenhang", "important_terms": [{"term": "Artikel 48", "explanation": "Notverordnungen"}], "sources": []}))
    db.add(briefing)
    db.commit()
    response = auth_client.post(f"/briefings/{briefing.id}/feedback", data={"feedback": "yes", "note": ""}, follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    memory = db.get(SubjectMemory, "ges")
    assert json.loads(memory.memory_json)["current_unit"] == "Artikel 48"
    assert json.loads(memory.memory_json)["last_confirmed_topic"] == "Artikel 48"
    assert memory.version == 2
