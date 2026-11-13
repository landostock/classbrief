"""Add optional interactive briefing clarifications.

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "briefing_clarifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lesson_id", sa.Integer(), sa.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", sa.String(length=80), sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("questions_json", sa.Text(), nullable=False),
        sa.Column("answers_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_briefing_clarifications_lesson_id", "briefing_clarifications", ["lesson_id"])
    op.create_index("ix_briefing_clarifications_subject_id", "briefing_clarifications", ["subject_id"])
    op.create_index("ix_briefing_clarifications_status", "briefing_clarifications", ["status"])
    op.create_index("ix_briefing_clarifications_created_at", "briefing_clarifications", ["created_at"])
    op.create_index("ix_briefing_clarifications_expires_at", "briefing_clarifications", ["expires_at"])


def downgrade():
    op.drop_table("briefing_clarifications")
