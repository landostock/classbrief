from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.openai_provider import AIProviderError
from app.ai.provider_factory import build_ai_provider
from app.config import Settings
from app.extractors import extract_document
from app.extractors.base import UnsupportedDocumentError
from app.models import Briefing, BriefingClarification, FileRecord, Lesson, MaterialAnalysis, ProcessingRun, Setting, Subject, SubjectMemory
from app.services.cost_tracker import automatic_budget_available, record_usage
from app.services.notifications import send_clarification_push
from app.services.schedule import previous_lesson
from app.services.webdav import WebDAVClient, WebDAVDownloadTooLarge, WebDAVError, WebDAVItem

logger = logging.getLogger("classbrief.prepare")
SUPPORTED = {".pdf", ".docx", ".pptx", ".txt", ".md", ".xlsx"}


class AlreadyProcessing(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _unchanged(record: FileRecord, item: WebDAVItem) -> bool:
    if record.etag and item.etag:
        return record.etag == item.etag
    return _aware(record.last_modified) == _aware(item.last_modified) and record.content_length == item.content_length


def _state_hash(subject_id: str, lesson_id: int, shas: list[str], memory_version: int, clarifications: list[dict] | None = None) -> str:
    raw = json.dumps([subject_id, lesson_id, sorted(shas), memory_version, clarifications or []], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _section_aware_limit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    boundary = text.rfind("\n", 0, limit)
    if boundary < limit // 2:
        boundary = text.rfind(". ", 0, limit)
    if boundary < limit // 2:
        boundary = limit
    return text[:boundary].rstrip() + "\n\n[Weiterer Dokumentinhalt für diese Analyse ausgelassen]"


def _batches(items: list[tuple[FileRecord, str, str]], max_chars: int) -> list[list[tuple[FileRecord, str, str]]]:
    batches: list[list[tuple[FileRecord, str, str]]] = []
    current: list[tuple[FileRecord, str, str]] = []
    size = 0
    for item in items:
        item_size = len(item[2])
        if current and size + item_size > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += item_size
    if current:
        batches.append(current)
    return batches


def _memory(db: Session, subject_id: str) -> SubjectMemory:
    memory = db.get(SubjectMemory, subject_id)
    if memory is None:
        memory = SubjectMemory(subject_id=subject_id, memory_json=json.dumps({"current_unit": "", "recent_topics": [], "important_concepts": [], "current_transition": "", "open_questions": []}))
        db.add(memory)
        db.flush()
    return memory


def acquire_lock(db: Session, subject_id: str, trigger: str, lesson_id: int | None, settings: Settings) -> ProcessingRun:
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=settings.processing_lock_timeout_minutes)
    active = db.scalar(select(ProcessingRun).where(ProcessingRun.subject_id == subject_id, ProcessingRun.status == "running").order_by(ProcessingRun.started_at.desc()))
    if active and _aware(active.started_at) and _aware(active.started_at) >= stale_before:
        raise AlreadyProcessing(subject_id)
    if active:
        active.status = "stale"
        active.completed_at = datetime.now(timezone.utc)
    run = ProcessingRun(trigger_type=trigger, subject_id=subject_id, lesson_id=lesson_id, status="running")
    db.add(run)
    db.commit()
    return run


async def _scan_subject(db: Session, subject: Subject, settings: Settings, window_start: datetime, automatic: bool) -> tuple[list[FileRecord], int]:
    if not settings.iserv_webdav_url or not settings.iserv_username or not settings.iserv_password:
        raise WebDAVError("IServ WebDAV ist nicht vollständig konfiguriert")
    client = WebDAVClient(settings.iserv_webdav_url, settings.iserv_username, settings.iserv_password)
    items = await client.walk_files(subject.webdav_path)
    changed: list[tuple[FileRecord, WebDAVItem]] = []
    now = datetime.now(timezone.utc)
    for item in items:
        record = db.scalar(select(FileRecord).where(FileRecord.subject_id == subject.id, FileRecord.relative_path == item.relative_path))
        if record is None:
            record = FileRecord(subject_id=subject.id, relative_path=item.relative_path, filename=item.filename)
            db.add(record)
            db.flush()
            changed.append((record, item))
        elif not _unchanged(record, item):
            changed.append((record, item))
        record.filename = item.filename
        record.etag = item.etag
        record.last_modified = item.last_modified
        record.content_length = item.content_length
        record.mime_type = item.content_type
        record.last_seen_at = now
    db.commit()

    analyzed: list[FileRecord] = []
    pending: list[tuple[FileRecord, str, str]] = []
    downloaded = 0
    provider, provider_name = build_ai_provider(db, settings)
    for record, item in changed:
        if Path(item.filename).suffix.lower() not in SUPPORTED:
            record.processing_status = "unsupported"
            db.commit()
            continue
        suffix = Path(item.filename).suffix.lower()
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = Path(tmp.name)
            download = await client.download_file_to_path(
                item.relative_path,
                tmp_path,
                max_bytes=settings.max_document_bytes,
            )
            downloaded += 1
            digest = download.sha256
            record.sha256 = digest
            duplicate = db.scalar(select(MaterialAnalysis).where(MaterialAnalysis.sha256 == digest))
            if duplicate:
                record.processing_status = "deduplicated"
                db.commit()
                analyzed.append(record)
                continue
            extracted = extract_document(tmp_path, max_chars=settings.max_document_chars)
            if extracted.status == "ocr_required":
                record.processing_status = "ocr_required"
                db.commit()
                continue
            text = _section_aware_limit(extracted.text, settings.max_document_chars)
            if settings.keep_extracted_text:
                record.extracted_text = text
            pending.append((record, item.filename, text))
            db.commit()
        except UnsupportedDocumentError:
            record.processing_status = "unsupported"
            db.commit()
        except WebDAVDownloadTooLarge as exc:
            record.processing_status = "too_large"
            record.error_message = str(exc)[:500]
            db.commit()
        except Exception as exc:
            record.processing_status = "ai_error" if isinstance(exc, AIProviderError) else "extract_error"
            record.error_message = str(exc)[:500]
            db.commit()
            if isinstance(exc, (AIProviderError, BudgetExceeded)):
                raise
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

    if pending and provider_name == "openai" and automatic and not automatic_budget_available(db, settings):
        for record, _, _ in pending:
            record.processing_status = "budget_blocked"
        db.commit()
        raise BudgetExceeded(subject.id)

    for batch in _batches(pending, settings.max_batch_chars):
        try:
            if len(batch) == 1:
                record, filename, text = batch[0]
                result = await provider.analyze_material(filename, text)
                values = {filename: result.value}
            else:
                result = await provider.analyze_materials([(filename, text) for _, filename, text in batch])
                values = {item.filename: item.analysis for item in result.value.materials}
            for record, filename, _ in batch:
                analysis_value = values.get(filename)
                if analysis_value is None:
                    raise AIProviderError(f"KI_ERROR: Analyse für {filename} fehlt")
                db.add(MaterialAnalysis(file_id=record.id, sha256=record.sha256, analysis_json=analysis_value.model_dump_json(), model=result.model))
                record.processing_status = "analyzed"
                analyzed.append(record)
            record_usage(db, model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens, purpose="material_analysis", provider=provider_name)
            db.commit()
        except Exception as exc:
            for record, _, _ in batch:
                record.processing_status = "ai_error"
                record.error_message = str(exc)[:500]
            db.commit()
            raise
    scan_key = f"last_scan:{subject.id}"
    setting = db.get(Setting, scan_key)
    if setting:
        setting.value = now.isoformat()
        setting.updated_at = now
    else:
        db.add(Setting(key=scan_key, value=now.isoformat()))
    db.commit()
    return analyzed, downloaded


async def _collect_lesson_context(db: Session, lesson: Lesson, settings: Settings, *, automatic: bool) -> dict:
    subject = db.get(Subject, lesson.subject_id)
    assert subject is not None
    previous = previous_lesson(db, subject.id, lesson.scheduled_start)
    confidence = "normal" if previous else "lower"
    scan_setting = db.get(Setting, f"last_scan:{subject.id}")
    fallback = datetime.now(timezone.utc) - timedelta(days=7)
    if scan_setting:
        try:
            fallback = datetime.fromisoformat(scan_setting.value)
        except ValueError:
            pass
    window_start = previous.scheduled_end if previous else fallback
    await _scan_subject(db, subject, settings, window_start, automatic=automatic)
    relevant = list(db.scalars(select(FileRecord).where(
        FileRecord.subject_id == subject.id,
        FileRecord.sha256.is_not(None),
        or_(FileRecord.last_modified >= window_start, FileRecord.first_seen_at >= window_start),
    )))
    analyses = []
    for record in relevant:
        analysis = db.scalar(select(MaterialAnalysis).where(MaterialAnalysis.sha256 == record.sha256))
        if analysis:
            analyses.append(json.loads(analysis.analysis_json))
    memory = _memory(db, subject.id)
    return {
        "subject": subject,
        "confidence": confidence,
        "relevant": relevant,
        "analyses": analyses,
        "memory": memory,
        "memory_data": json.loads(memory.memory_json or "{}"),
    }


def clarification_payload(item: BriefingClarification) -> dict:
    questions = json.loads(item.questions_json or "[]")
    return {
        "id": item.id,
        "lesson_id": item.lesson_id,
        "subject": item.subject.name,
        "questions": questions,
        "expires_at": item.expires_at.isoformat(),
    }


async def prepare_clarifications(db: Session, lesson: Lesson, settings: Settings) -> dict:
    """Inspect a manual briefing and ask only questions that can change it."""
    now = datetime.now(timezone.utc)
    pending = db.scalar(
        select(BriefingClarification)
        .where(
            BriefingClarification.lesson_id == lesson.id,
            BriefingClarification.status == "pending",
            BriefingClarification.expires_at > now,
        )
        .order_by(BriefingClarification.created_at.desc())
        .limit(1)
    )
    if pending:
        return {"status": "questions", "clarification": clarification_payload(pending)}

    run = acquire_lock(db, lesson.subject_id, "clarification", lesson.id, settings)
    try:
        context = await _collect_lesson_context(db, lesson, settings, automatic=False)
        shas = sorted({record.sha256 for record in context["relevant"] if record.sha256})
        base_hash = _state_hash(lesson.subject_id, lesson.id, shas, context["memory"].version)
        existing = db.scalar(
            select(Briefing).where(
                Briefing.lesson_id == lesson.id,
                Briefing.material_state_hash == base_hash,
                Briefing.status == "ready",
            )
        )
        if existing:
            run.status = "cached"
            run.completed_at = now
            db.commit()
            return {"status": "ready", "briefing_id": existing.id}

        provider, provider_name = build_ai_provider(db, settings)
        result = await provider.create_clarifications(
            context["subject"].name,
            context["memory_data"],
            context["analyses"],
        )
        record_usage(
            db,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            purpose="briefing_clarifications",
            provider=provider_name,
        )
        questions: list[dict] = []
        for question in result.value.questions[:3]:
            data = question.model_dump()
            known = [option for option in data["options"] if option["id"] != "unknown"][:4]
            data["options"] = [*known, {"id": "unknown", "label": "Weiß ich nicht – vorsichtig ableiten"}]
            questions.append(data)
        if not questions:
            run.status = "no_questions"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return {"status": "no_questions"}

        expires_at = now + timedelta(hours=4)
        request = BriefingClarification(
            lesson_id=lesson.id,
            subject_id=lesson.subject_id,
            questions_json=json.dumps(questions, ensure_ascii=False),
            status="pending",
            expires_at=expires_at,
        )
        db.add(request)
        run.status = "awaiting_input"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(request)
        await send_clarification_push(db, request, settings)
        return {"status": "questions", "clarification": clarification_payload(request)}
    except Exception as exc:
        db.rollback()
        persisted_run = db.get(ProcessingRun, run.id)
        if persisted_run:
            persisted_run.status = "failed"
            persisted_run.error_code = type(exc).__name__
            persisted_run.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise


async def prepare_lesson(db: Session, lesson: Lesson, settings: Settings, trigger: str = "manual", force_budget: bool = False, clarification_answers: list[dict] | None = None) -> tuple[str, Briefing]:
    run = acquire_lock(db, lesson.subject_id, trigger, lesson.id, settings)
    try:
        context = await _collect_lesson_context(db, lesson, settings, automatic=trigger == "automatic" and not force_budget)
        subject = context["subject"]
        relevant = context["relevant"]
        shas = sorted({record.sha256 for record in relevant if record.sha256})
        memory = context["memory"]
        state_hash = _state_hash(subject.id, lesson.id, shas, memory.version, clarification_answers)
        existing = db.scalar(select(Briefing).where(Briefing.lesson_id == lesson.id, Briefing.material_state_hash == state_hash, Briefing.status == "ready"))
        if existing:
            run.status = "cached"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return "cached", existing

        analyses = context["analyses"]
        memory_data = context["memory_data"]
        if not analyses and not clarification_answers:
            current = memory_data.get("current_unit") or "Kein neues Material"
            quick = "Seit der letzten Unterrichtseinheit wurden keine neuen auswertbaren Materialien gefunden. Nutze das Fachgedächtnis als Orientierung."
            medium = quick + (f"\n\nAktueller Themenstand: {current}." if current != "Kein neues Material" else "")
            deep = {"summary": quick, "prior_knowledge": memory_data.get("recent_topics", []), "new_materials": [], "important_terms": memory_data.get("important_concepts", []), "likely_tasks": [], "pitfalls": [], "sources": []}
            briefing_data = {"probable_topic": current, "quick_brief": quick, "medium_brief": medium, "deep": deep}
        else:
            provider, provider_name = build_ai_provider(db, settings)
            if provider_name == "openai" and trigger == "automatic" and not force_budget and not automatic_budget_available(db, settings):
                raise BudgetExceeded(subject.id)
            result = await provider.create_lesson_briefing(subject.name, memory_data, analyses, clarifications=clarification_answers)
            record_usage(db, model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens, purpose="lesson_briefing", provider=provider_name)
            briefing_data = result.value.model_dump()
        briefing = Briefing(
            lesson_id=lesson.id,
            subject_id=subject.id,
            material_state_hash=state_hash,
            probable_topic=briefing_data["probable_topic"],
            quick_brief=briefing_data["quick_brief"],
            medium_brief=briefing_data["medium_brief"],
            deep_brief_json=json.dumps(briefing_data["deep"], ensure_ascii=False),
            status="ready",
            confidence=context["confidence"],
        )
        db.add(briefing)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            cached = db.scalar(select(Briefing).where(Briefing.lesson_id == lesson.id, Briefing.material_state_hash == state_hash))
            if cached is None:
                raise
            return "cached", cached
        run.status = "success"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("briefing.generated", extra={"subject_id": subject.id, "lesson_id": lesson.id})
        return "updated", briefing
    except Exception as exc:
        db.rollback()
        persisted_run = db.get(ProcessingRun, run.id)
        if persisted_run:
            persisted_run.status = "failed"
            persisted_run.error_code = type(exc).__name__
            persisted_run.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise
