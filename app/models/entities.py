from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    short_name: Mapped[str] = mapped_column(String(24))
    webdav_path: Mapped[str] = mapped_column(String(500))
    preparation_minutes: Mapped[int] = mapped_column(Integer, default=20)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    briefing_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    color_identifier: Mapped[str] = mapped_column(String(32), default="indigo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer, index=True)  # Monday = 0
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    subject: Mapped[Subject] = relationship()


class ScheduleException(Base):
    __tablename__ = "schedule_exceptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    exception_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # cancel, move, add, global_disabled_date
    schedule_entry_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_entries.id", ondelete="CASCADE"), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), nullable=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    subject: Mapped[Subject | None] = relationship()


class AutomationRule(Base):
    __tablename__ = "automation_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    rule_type: Mapped[str] = mapped_column(String(32), index=True)
    weekdays: Mapped[str] = mapped_column(String(32), default="0,1,2,3,4")
    run_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    target_start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    subject_id: Mapped[str | None] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), nullable=True, index=True)
    lead_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    subject: Mapped[Subject | None] = relationship()


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    automation_key: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    target_count: Mapped[int] = mapped_column(Integer, default=0)
    briefing_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FileRecord(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("subject_id", "relative_path", name="uq_file_subject_path"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    relative_path: Mapped[str] = mapped_column(String(800))
    filename: Mapped[str] = mapped_column(String(300))
    etag: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processing_status: Mapped[str] = mapped_column(String(40), default="discovered")
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class MaterialAnalysis(Base):
    __tablename__ = "material_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    analysis_json: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    file: Mapped[FileRecord] = relationship()


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("subject_id", "scheduled_start", name="uq_lesson_subject_start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), default="schedule")
    status: Mapped[str] = mapped_column(String(32), default="scheduled")
    room: Mapped[str | None] = mapped_column(String(120), nullable=True)
    teacher: Mapped[str | None] = mapped_column(String(120), nullable=True)
    change_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subject: Mapped[Subject] = relationship()


class Briefing(Base):
    __tablename__ = "briefings"
    __table_args__ = (UniqueConstraint("lesson_id", "material_state_hash", name="uq_briefing_lesson_state"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    material_state_hash: Mapped[str] = mapped_column(String(64), index=True)
    probable_topic: Mapped[str] = mapped_column(String(300))
    quick_brief: Mapped[str] = mapped_column(Text)
    medium_brief: Mapped[str] = mapped_column(Text)
    deep_brief_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="ready")
    confidence: Mapped[str] = mapped_column(String(24), default="normal")
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    lesson: Mapped[Lesson] = relationship()
    subject: Mapped[Subject] = relationship()


class BriefingMaterial(Base):
    __tablename__ = "briefing_materials"
    __table_args__ = (UniqueConstraint("briefing_id", "sha256", name="uq_briefing_material_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    briefing_id: Mapped[int] = mapped_column(ForeignKey("briefings.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    briefing: Mapped[Briefing] = relationship()
    file: Mapped[FileRecord] = relationship()


class SubjectMemory(Base):
    __tablename__ = "subject_memory"
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True)
    memory_json: Mapped[str] = mapped_column(Text, default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str | None] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), nullable=True, index=True)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    purpose: Mapped[str] = mapped_column(String(80))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ChatGPTConnection(Base):
    """One encrypted ChatGPT-plan authorization for this single-user app."""

    __tablename__ = "chatgpt_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    openai_subject: Mapped[str] = mapped_column(String(255), index=True)
    client_id: Mapped[str] = mapped_column(String(255), unique=True)
    host_id: Mapped[str] = mapped_column(String(255), unique=True)
    encrypted_credentials: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text, default="")
    selected_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="connected", index=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatGPTPairing(Base):
    """Short-lived, one-use bridge between local OAuth and the Fly runtime."""

    __tablename__ = "chatgpt_pairings"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    host_id: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BriefingClarification(Base):
    """A short-lived set of optional questions before a manual briefing."""

    __tablename__ = "briefing_clarifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    questions_json: Mapped[str] = mapped_column(Text)
    answers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lesson: Mapped[Lesson] = relationship()
    subject: Mapped[Subject] = relationship()
