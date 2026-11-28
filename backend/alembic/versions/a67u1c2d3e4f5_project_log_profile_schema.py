"""ADR-0067 U1 — TestProject.log_profile additive schema (#4182 Phase 3).

Adds structured project-level continuous-logging preset columns to
``test_project``:
- ``log_profile`` (JSONB, nullable, default NULL — zero existing behavior drift)
- ``log_profile_version`` (INTEGER, NOT NULL, server_default 0, CHECK >= 0)

Pure additive; ``downgrade()`` fully reverses. Head prior: ``b13u1c2d3e4f5``.

Revision ID: a67u1c2d3e4f5
Revises:    b13u1c2d3e4f5
Create Date: 2026-11-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a67u1c2d3e4f5"
down_revision = "b13u1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_project",
        sa.Column(
            "log_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "test_project",
        sa.Column(
            "log_profile_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_test_project_log_profile_version_nonneg",
        "test_project",
        "log_profile_version >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_test_project_log_profile_version_nonneg",
        "test_project",
        type_="check",
    )
    op.drop_column("test_project", "log_profile_version")
    op.drop_column("test_project", "log_profile")
