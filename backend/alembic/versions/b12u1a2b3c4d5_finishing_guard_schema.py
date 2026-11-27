"""B12 U1 — FinishingGuard additive schema (ADR-0065 / #4206 v1.1).

- ``job_instance.finishing_guard`` / ``negotiated_capabilities`` (JSONB, nullable)
- ``plan_step.atomic_group`` / ``skip_satisfies_delivery``

Note (v1.1)：全局单行准入闸门表 ``finishing_guard_admission`` 归 U4 与真实持锁
消费方同 PR 落地，不在 U1 建无消费方模型（orphan-models）。

Pure additive; ``downgrade()`` fully reverses. Head prior: ``b8u5a1c2d3e4``.

Revision ID: b12u1a2b3c4d5
Revises:    b8u5a1c2d3e4
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b12u1a2b3c4d5"
down_revision = "b8u5a1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_instance",
        sa.Column("finishing_guard", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "job_instance",
        sa.Column(
            "negotiated_capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "plan_step",
        sa.Column("atomic_group", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "plan_step",
        sa.Column(
            "skip_satisfies_delivery",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("plan_step", "skip_satisfies_delivery")
    op.drop_column("plan_step", "atomic_group")
    op.drop_column("job_instance", "negotiated_capabilities")
    op.drop_column("job_instance", "finishing_guard")
