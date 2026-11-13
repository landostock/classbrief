"""Add encrypted ChatGPT plan connections and one-time pairing.

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chatgpt_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("openai_subject", sa.String(length=255), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("host_id", sa.String(length=255), nullable=False),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("selected_model", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="connected"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id"),
        sa.UniqueConstraint("host_id"),
    )
    op.create_index("ix_chatgpt_connections_openai_subject", "chatgpt_connections", ["openai_subject"])
    op.create_index("ix_chatgpt_connections_status", "chatgpt_connections", ["status"])
    op.create_table(
        "chatgpt_pairings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("host_id", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_chatgpt_pairings_token_hash", "chatgpt_pairings", ["token_hash"])
    op.create_index("ix_chatgpt_pairings_expires_at", "chatgpt_pairings", ["expires_at"])


def downgrade():
    op.drop_table("chatgpt_pairings")
    op.drop_table("chatgpt_connections")
