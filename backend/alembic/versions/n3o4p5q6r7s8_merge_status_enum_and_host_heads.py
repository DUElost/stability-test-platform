"""Merge status-enum migration with host hardening head.

Revision ID: n3o4p5q6r7s8
Revises: l2m3n4o5p6q7, w1x2y3z4a5b6
Create Date: 2026-05-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "n3o4p5q6r7s8"
down_revision = ("l2m3n4o5p6q7", "w1x2y3z4a5b6")
branch_labels = None
depends_on = None


PLAN_RUN_STATUS_VALUES = (
    "RUNNING",
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "DEGRADED",
)


def _plan_run_uses_native_enum(bind) -> bool:
    if bind.dialect.name != "postgresql":
        return False
    result = bind.execute(sa.text("""
        SELECT udt_name = 'plan_run_status'
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'plan_run'
           AND column_name = 'status'
    """))
    return bool(result.scalar())


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "plan_run" not in inspector.get_table_names():
        return

    # w1x2y3z4a5b6 is parallel to the ADR-0020 branch and therefore cannot
    # alter plan_run on a fresh database.  Once both branches meet here the
    # table and job_instance.plan_run_id are guaranteed to exist.
    if bind.dialect.name == "postgresql":
        if not _plan_run_uses_native_enum(bind):
            plan_run_status_enum = postgresql.ENUM(
                *PLAN_RUN_STATUS_VALUES,
                name="plan_run_status",
            )
            plan_run_status_enum.create(bind, checkfirst=True)
            op.execute("ALTER TABLE plan_run ALTER COLUMN status DROP DEFAULT")
            op.execute(
                "ALTER TABLE plan_run "
                "ALTER COLUMN status TYPE plan_run_status USING status::plan_run_status"
            )
            op.execute(
                "ALTER TABLE plan_run ALTER COLUMN status SET DEFAULT 'RUNNING'"
            )
    else:
        status_type = next(
            column["type"]
            for column in inspector.get_columns("plan_run")
            if column["name"] == "status"
        )
        if not isinstance(status_type, sa.Enum):
            with op.batch_alter_table("plan_run") as batch_op:
                batch_op.alter_column(
                    "status",
                    existing_type=status_type,
                    type_=sa.Enum(
                        *PLAN_RUN_STATUS_VALUES,
                        name="plan_run_status",
                        native_enum=False,
                    ),
                    existing_nullable=False,
                    server_default="RUNNING",
                )

    indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("job_instance")
    }
    if "idx_job_instance_plan_run_status" not in indexes:
        op.create_index(
            "idx_job_instance_plan_run_status",
            "job_instance",
            ["plan_run_id", "status"],
        )


def downgrade() -> None:
    pass
