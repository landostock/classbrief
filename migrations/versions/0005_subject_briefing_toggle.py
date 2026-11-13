"""Allow subjects to stay on the timetable without receiving briefings.

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "subjects",
        sa.Column("briefing_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Sport remains a visible timetable subject, but never creates a briefing.
    op.execute(
        "UPDATE subjects SET enabled = 1, briefing_enabled = 0 "
        "WHERE lower(id) = 'sport' OR lower(name) = 'sport'"
    )


def downgrade():
    op.drop_column("subjects", "briefing_enabled")
