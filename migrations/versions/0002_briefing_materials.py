"""Track which material versions were already used in a briefing.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "briefing_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("briefing_id", sa.Integer(), sa.ForeignKey("briefings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("briefing_id", "sha256", name="uq_briefing_material_hash"),
    )
    op.create_index("ix_briefing_materials_briefing_id", "briefing_materials", ["briefing_id"])
    op.create_index("ix_briefing_materials_file_id", "briefing_materials", ["file_id"])
    op.create_index("ix_briefing_materials_sha256", "briefing_materials", ["sha256"])


def downgrade():
    op.drop_index("ix_briefing_materials_sha256", table_name="briefing_materials")
    op.drop_index("ix_briefing_materials_file_id", table_name="briefing_materials")
    op.drop_index("ix_briefing_materials_briefing_id", table_name="briefing_materials")
    op.drop_table("briefing_materials")
