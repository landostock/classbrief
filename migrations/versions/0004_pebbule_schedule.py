"""Add authoritative Pebbule lesson details.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("lessons", sa.Column("room", sa.String(length=120), nullable=True))
    op.add_column("lessons", sa.Column("teacher", sa.String(length=120), nullable=True))
    op.add_column("lessons", sa.Column("change_kind", sa.String(length=32), nullable=True))
    op.add_column("lessons", sa.Column("note", sa.String(length=500), nullable=True))


def downgrade():
    op.drop_column("lessons", "note")
    op.drop_column("lessons", "change_kind")
    op.drop_column("lessons", "teacher")
    op.drop_column("lessons", "room")
