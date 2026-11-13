"""Add configurable cloud automation rules and run status.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("rule_type", sa.String(length=32), nullable=False),
        sa.Column("weekdays", sa.String(length=32), nullable=False),
        sa.Column("run_time", sa.Time(), nullable=True),
        sa.Column("target_start_time", sa.Time(), nullable=True),
        sa.Column("subject_id", sa.String(length=80), sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("lead_minutes", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_automation_rules_rule_type", "automation_rules", ["rule_type"])
    op.create_index("ix_automation_rules_subject_id", "automation_rules", ["subject_id"])
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("automation_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("briefing_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_automation_runs_automation_key", "automation_runs", ["automation_key"])
    op.create_index("ix_automation_runs_status", "automation_runs", ["status"])
    op.create_index("ix_automation_runs_created_at", "automation_runs", ["created_at"])


def downgrade():
    op.drop_index("ix_automation_runs_created_at", table_name="automation_runs")
    op.drop_index("ix_automation_runs_status", table_name="automation_runs")
    op.drop_index("ix_automation_runs_automation_key", table_name="automation_runs")
    op.drop_table("automation_runs")
    op.drop_index("ix_automation_rules_subject_id", table_name="automation_rules")
    op.drop_index("ix_automation_rules_rule_type", table_name="automation_rules")
    op.drop_table("automation_rules")
