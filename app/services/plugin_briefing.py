from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.schemas import LessonBriefingSchema
from app.config import Settings
from app.extractors import extract_document
from app.extractors.base import UnsupportedDocumentError
from app.models import Briefing, BriefingMaterial, FileRecord, Lesson, Setting, SubjectMemory
from app.services.schedule import BERLIN, previous_lesson
from app.services.webdav import WebDAVClient, WebDAVDownloadTooLarge, WebDAVError, WebDAVItem

SUPPORTED = {".pdf", ".docx", ".pptx", ".txt", ".md", ".xlsx"}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _changed(record: FileRecord, item: WebDAVItem) -> bool:
    if record.etag and item.etag:
        return record.etag != item.etag
    return _aware(record.last_modified) != _aware(item.last_modified) or record.content_length != item.content_length


def _bounded_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    boundary = text.rfind("\n", 0, limit)
    if boundary < limit // 2:
        boundary = text.rfind(". ", 0, limit)
    if boundary < limit // 2:
        boundary = limit
    return text[:boundary].rstrip() + "\n\n[Weiterer Inhalt wurde an einer Abschnittsgrenze ausgelassen.]"


def _window_start(db: Session, lesson: Lesson, now: datetime) -> tuple[datetime, str, Lesson | None]:
    previous = previous_lesson(db, lesson.subject_id, lesson.scheduled_start)
    memory = db.get(SubjectMemory, lesson.subject_id)
    confirmed = _aware(memory.confirmed_until) if memory else None
    candidates = [_aware(previous.scheduled_end) if previous else None, confirmed]
    candidates = [value for value in candidates if value is not None and value <= now.astimezone(timezone.utc)]
    if candidates:
        return max(candidates), "normal", previous

    last_scan = db.get(Setting, f"last_scan:{lesson.subject_id}")
    fallback = now.astimezone(timezone.utc) - timedelta(days=7)
    if last_scan:
        try:
            fallback = max(fallback, _aware(datetime.fromisoformat(last_scan.value)) or fallback)
        except ValueError:
            pass
    return fallback, "lower", previous


def _material_was_briefed(db: Session, subject_id: str, sha256: str) -> bool:
    return db.scalar(
        select(BriefingMaterial.id)
        .join(Briefing, Briefing.id == BriefingMaterial.briefing_id)
        .where(Briefing.subject_id == subject_id, BriefingMaterial.sha256 == sha256)
        .limit(1)
    ) is not None


def _recent_confirmed_briefings(db: Session, lesson: Lesson) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(Briefing)
            .join(Lesson, Lesson.id == Briefing.lesson_id)
            .where(
                Briefing.subject_id == lesson.subject_id,
                Briefing.feedback.in_(["yes", "partial"]),
                Lesson.scheduled_end < lesson.scheduled_start,
            )
            .order_by(Lesson.scheduled_end.desc())
            .limit(3)
        )
    )
    return [
        {
            "lesson_end": item.lesson.scheduled_end.isoformat(),
            "probable_topic": item.probable_topic,
            "feedback": item.feedback,
            "what_was_actually_covered": item.feedback_note,
        }
        for item in rows
    ]


async def collect_lesson_context(
    db: Session,
    lesson: Lesson,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return only current, not-yet-briefed material for one lesson.

    The first scan establishes a baseline: old files already present in the folder
    are catalogued but never sent to the model merely because they were first seen.
    """

    if not settings.iserv_webdav_url or not settings.iserv_username or not settings.iserv_password:
        raise WebDAVError("IServ WebDAV ist nicht vollständig konfiguriert")

    now = now or datetime.now(BERLIN)
    now_utc = now.astimezone(timezone.utc)
    subject = lesson.subject
    window_start, confidence, previous = _window_start(db, lesson, now)
    scan_key = f"last_scan:{subject.id}"
    initial_baseline = db.get(Setting, scan_key) is None
    client = WebDAVClient(settings.iserv_webdav_url, settings.iserv_username, settings.iserv_password)
    items = await client.walk_files(subject.webdav_path)
    eligible: list[tuple[FileRecord, WebDAVItem]] = []

    for item in items:
        record = db.scalar(
            select(FileRecord).where(
                FileRecord.subject_id == subject.id,
                FileRecord.relative_path == item.relative_path,
            )
        )
        is_new = record is None
        if record is None:
            record = FileRecord(subject_id=subject.id, relative_path=item.relative_path, filename=item.filename)
            db.add(record)
            db.flush()
            did_change = True
        else:
            did_change = _changed(record, item)

        previous_status = record.processing_status
        record.filename = item.filename
        record.etag = item.etag
        record.last_modified = item.last_modified
        record.content_length = item.content_length
        record.mime_type = item.content_type
        record.last_seen_at = now_utc

        suffix = Path(item.filename).suffix.lower()
        modified = _aware(item.last_modified)
        in_window = bool(modified and modified >= window_start)

        if suffix not in SUPPORTED:
            record.processing_status = "unsupported"
        elif is_new and initial_baseline and not in_window:
            # Do not mistake a whole pre-existing school-year folder for new work.
            record.processing_status = "historical"
        elif did_change and (not initial_baseline or in_window):
            record.processing_status = "plugin_pending"
            eligible.append((record, item))
        elif previous_status in {
            "plugin_pending",
            "context_ready",
            "download_error",
            "extract_error",
            "too_large",
        }:
            # Keep an eligible material available until a briefing actually saves
            # its hash. Advancing last_scan must not consume an unsaved context.
            eligible.append((record, item))

    db.commit()

    materials: list[dict[str, Any]] = []
    material_issues: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    remaining = max(0, settings.plugin_max_context_chars)
    for record, item in eligible:
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=Path(item.filename).suffix.lower(), delete=False) as tmp:
                tmp_path = Path(tmp.name)
            download = await client.download_file_to_path(
                item.relative_path,
                tmp_path,
                max_bytes=settings.max_document_bytes,
            )
            digest = download.sha256
            record.sha256 = digest
            if digest in seen_hashes or _material_was_briefed(db, subject.id, digest):
                record.processing_status = "already_briefed"
                db.commit()
                continue
            if remaining <= 0:
                record.processing_status = "context_limit"
                db.commit()
                continue

            extracted = extract_document(
                tmp_path,
                max_chars=min(settings.max_document_chars, remaining),
            )
            if extracted.status == "ocr_required":
                record.processing_status = "ocr_required"
                db.commit()
                continue

            text = _bounded_text(extracted.text, min(settings.max_document_chars, remaining))
            if extracted.truncated:
                text += "\n\n[Weitere Seiten oder Abschnitte wurden an der Kontextgrenze ausgelassen.]"
            remaining -= len(text)
            seen_hashes.add(digest)
            record.processing_status = "context_ready"
            if settings.keep_extracted_text:
                record.extracted_text = text
            materials.append(
                {
                    "file_id": record.id,
                    "sha256": digest,
                    "filename": item.filename,
                    "last_modified": modified.isoformat() if modified else None,
                    "content": text,
                }
            )
            db.commit()
        except UnsupportedDocumentError:
            record.processing_status = "unsupported"
            db.commit()
        except WebDAVDownloadTooLarge as exc:
            record.processing_status = "too_large"
            record.error_message = str(exc)[:500]
            material_issues.append({"filename": item.filename, "stage": "download", "message": str(exc)})
            db.commit()
        except Exception as exc:
            record.processing_status = "download_error" if isinstance(exc, WebDAVError) else "extract_error"
            record.error_message = str(exc)[:500]
            material_issues.append(
                {
                    "filename": item.filename,
                    "stage": "download" if isinstance(exc, WebDAVError) else "extraction",
                    "message": str(exc),
                }
            )
            db.commit()
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

    stamp = now_utc.isoformat()
    scan_setting = db.get(Setting, scan_key)
    if scan_setting:
        scan_setting.value = stamp
        scan_setting.updated_at = now_utc
    else:
        db.add(Setting(key=scan_key, value=stamp))
    db.commit()

    memory = db.get(SubjectMemory, subject.id)
    memory_data = json.loads(memory.memory_json or "{}") if memory else {}
    return {
        "lesson": {
            "id": lesson.id,
            "subject_id": subject.id,
            "subject": subject.name,
            "starts_at": lesson.scheduled_start.isoformat(),
            "ends_at": lesson.scheduled_end.isoformat(),
            "room": lesson.room,
            "teacher": lesson.teacher,
            "schedule_status": lesson.status,
            "change_kind": lesson.change_kind,
            "change_note": lesson.note,
            "schedule_source": lesson.source,
        },
        "selection": {
            "from": window_start.isoformat(),
            "to": now_utc.isoformat(),
            "confidence": confidence,
            "initial_baseline": initial_baseline,
            "rule": "Only files newly added or changed in this window and never used in an earlier briefing are included.",
        },
        "previous_lesson": (
            {
                "starts_at": previous.scheduled_start.isoformat(),
                "ends_at": previous.scheduled_end.isoformat(),
            }
            if previous
            else None
        ),
        "subject_memory": memory_data,
        "confirmed_recent_lessons": _recent_confirmed_briefings(db, lesson),
        "materials": materials,
        "material_issues": material_issues,
        "source_hashes": [item["sha256"] for item in materials],
        "guardrails": [
            "Do not infer future school-year topics from files outside materials.",
            "Treat confirmed recent topics as prior knowledge, not automatically as the next topic.",
            "If a material conflicts with the confirmed current unit, flag the uncertainty instead of forcing it into the briefing.",
        ],
    }


def save_plugin_briefing_with_status(
    db: Session,
    lesson: Lesson,
    payload: dict[str, Any],
    source_hashes: list[str],
    *,
    confidence: str = "normal",
) -> tuple[Briefing, bool]:
    value = LessonBriefingSchema.model_validate(payload)
    memory = db.get(SubjectMemory, lesson.subject_id)
    memory_version = memory.version if memory else 0
    requested_hashes = sorted(set(source_hashes))
    records = list(
        db.scalars(
            select(FileRecord).where(
                FileRecord.subject_id == lesson.subject_id,
                FileRecord.sha256.in_(requested_hashes),
            )
        )
    ) if requested_hashes else []
    by_hash: dict[str, FileRecord] = {}
    for record in records:
        if record.sha256:
            by_hash.setdefault(record.sha256, record)
    # Never let a model invent source hashes that were not observed for this subject.
    hashes = sorted(by_hash)
    if not hashes:
        latest = db.scalar(
            select(Briefing)
            .where(Briefing.lesson_id == lesson.id, Briefing.status == "ready")
            .order_by(Briefing.created_at.desc())
            .limit(1)
        )
        if latest:
            return latest, False
    state_raw = json.dumps([lesson.subject_id, lesson.id, hashes, memory_version], separators=(",", ":"))
    state_hash = hashlib.sha256(state_raw.encode()).hexdigest()
    existing = db.scalar(
        select(Briefing).where(
            Briefing.lesson_id == lesson.id,
            Briefing.material_state_hash == state_hash,
            Briefing.status == "ready",
        )
    )
    if existing:
        return existing, False

    briefing = Briefing(
        lesson_id=lesson.id,
        subject_id=lesson.subject_id,
        material_state_hash=state_hash,
        probable_topic=value.probable_topic,
        quick_brief=value.quick_brief,
        medium_brief=value.medium_brief,
        deep_brief_json=value.deep.model_dump_json(),
        status="ready",
        confidence=confidence if confidence in {"normal", "lower"} else "normal",
    )
    db.add(briefing)
    db.flush()
    if hashes:
        for digest in hashes:
            record = by_hash.get(digest)
            if record:
                db.add(BriefingMaterial(briefing_id=briefing.id, file_id=record.id, sha256=digest))
                record.processing_status = "already_briefed"
    db.commit()
    return briefing, True


def save_plugin_briefing(
    db: Session,
    lesson: Lesson,
    payload: dict[str, Any],
    source_hashes: list[str],
    *,
    confidence: str = "normal",
) -> Briefing:
    briefing, _created = save_plugin_briefing_with_status(
        db,
        lesson,
        payload,
        source_hashes,
        confidence=confidence,
    )
    return briefing
