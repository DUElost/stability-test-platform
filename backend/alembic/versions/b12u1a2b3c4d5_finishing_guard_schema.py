"""B12 U1 — FinishingGuard additive schema (ADR-0065).

- ``job_instance.finishing_guard`` / ``negotiated_capabilities`` (JSONB, nullable)
- ``plan_step.atomic_group`` / ``skip_satisfies_delivery``
- singleton ``finishing_guard_admission`` row (id=1, admission_open=true)

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
    op.create_table(
        "finishing_guard_admission",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "admission_open",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id = 1", name="ck_finishing_guard_admission_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO finishing_guard_admission (id, admission_open) VALUES (1, true)"
    )


def downgrade() -> None:
    op.drop_table("finishing_guard_admission")
    op.drop_column("plan_step", "skip_satisfies_delivery")
    op.drop_column("plan_step", "atomic_group")
    op.drop_column("job_instance", "negotiated_capabilities")
    op.drop_column("job_instance", "finishing_guard")
