"""Initial schema.

Revision ID: 0001
"""
from alembic import op
from app.database import Base
import app.models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # This historical migration intentionally creates only tables owned by
    # revision 0001. Newer models must stay in their own revisions even though
    # Base.metadata contains the current application schema.
    later_tables = {
        "briefing_materials",
        "automation_rules",
        "automation_runs",
        "chatgpt_connections",
        "chatgpt_pairings",
        "briefing_clarifications",
    }
    initial_tables = [table for table in Base.metadata.sorted_tables if table.name not in later_tables]
    Base.metadata.create_all(bind=bind, tables=initial_tables)


def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
