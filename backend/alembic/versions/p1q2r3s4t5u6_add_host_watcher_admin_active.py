"""add watcher_admin_active to host

Revision ID: p1q2r3s4t5u6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "p1q2r3s4t5u6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column(
            "watcher_admin_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.alter_column("host", "watcher_admin_active", server_default=None)


def downgrade() -> None:
    op.drop_column("host", "watcher_admin_active")
