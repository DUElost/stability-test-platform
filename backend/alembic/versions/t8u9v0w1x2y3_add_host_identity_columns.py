"""Add boot_id and last_agent_instance_id to host (ADR-0019 Phase 3a).

Revision ID: t8u9v0w1x2y3
Revises:    s7t8u9v0w1x2
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa

revision = "t8u9v0w1x2y3"
down_revision = "s7t8u9v0w1x2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column(
            "boot_id",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.add_column(
        "host",
        sa.Column(
            "last_agent_instance_id",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )


def downgrade() -> None:
    op.drop_column("host", "last_agent_instance_id")
    op.drop_column("host", "boot_id")
