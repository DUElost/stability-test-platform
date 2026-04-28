"""Add script sequence templates.

Revision ID: p4q5r6s7t8u9
Revises:    o3i4j5k6l7m8
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "p4q5r6s7t8u9"
down_revision = "o3i4j5k6l7m8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "script_sequence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("on_failure", sa.String(length=16), nullable=False, server_default="stop"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_script_sequence_created_at", "script_sequence", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_script_sequence_created_at", table_name="script_sequence")
    op.drop_table("script_sequence")
