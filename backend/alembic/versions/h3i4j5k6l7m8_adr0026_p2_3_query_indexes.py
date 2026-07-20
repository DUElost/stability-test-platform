"""ADR-0026 P2-3 — admission / target-device query indexes.

1. Recreate ``idx_plan_run_admission_queue`` to match pump ORDER BY
   ``priority DESC, enqueued_at ASC`` (``next_admission_at`` stays a WHERE
   filter, not a sort key — especially with aging boost).
2. Add ``idx_prtd_plan_run_sort`` for ordered target-device scans at
   admission / matrix joins.

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "h3i4j5k6l7m8"
down_revision = "g2h3i4j5k6l7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_index("idx_plan_run_admission_queue", table_name="plan_run")
    op.create_index(
        "idx_plan_run_admission_queue",
        "plan_run",
        [sa.text("priority DESC"), sa.text("enqueued_at ASC")],
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    op.create_index(
        "idx_prtd_plan_run_sort",
        "plan_run_target_device",
        ["plan_run_id", "sort_order"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_index("idx_prtd_plan_run_sort", table_name="plan_run_target_device")
    op.drop_index("idx_plan_run_admission_queue", table_name="plan_run")
    op.create_index(
        "idx_plan_run_admission_queue",
        "plan_run",
        ["priority", "next_admission_at", "enqueued_at"],
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
