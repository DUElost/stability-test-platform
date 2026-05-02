"""Drop device.lock_run_id and lock_expires_at (ADR-0019 Phase 6d-3).

Locking is fully managed by device_leases since Phase 2c.
These projection columns are no longer written (Phase 6d) or read (Phase 6c).

Revision ID: u9v0w1x2y3z4
Revises:    t8u9v0w1x2y3
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa

revision = "u9v0w1x2y3z4"
down_revision = "t8u9v0w1x2y3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("device", "lock_expires_at")
    op.drop_column("device", "lock_run_id")


def downgrade() -> None:
    op.add_column("device", sa.Column("lock_run_id", sa.Integer(), nullable=True))
    op.add_column(
        "device",
        sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
