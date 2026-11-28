"""B12 U4 — global FinishingGuard admission gate (ADR-0065 / #4206).

Revision ID: b12u4a1b2c3d4
Revises: b13u1c2d3e4f5
Create Date: 2026-11-28
"""

from alembic import op
import sqlalchemy as sa

revision = "b12u4a1b2c3d4"
down_revision = "b13u1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finishing_guard_admission",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "admission_open",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.CheckConstraint("id = 1", name="ck_finishing_guard_admission_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO finishing_guard_admission (id, admission_open) VALUES (1, true)"
    )


def downgrade() -> None:
    op.drop_table("finishing_guard_admission")