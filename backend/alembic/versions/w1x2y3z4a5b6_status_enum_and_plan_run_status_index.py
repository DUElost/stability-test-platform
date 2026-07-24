"""Promote PlanRun/JobInstance status columns to enums and add hot-path index.

Revision ID: w1x2y3z4a5b6
Revises: v0w1x2y3z4a5
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "w1x2y3z4a5b6"
down_revision = "v0w1x2y3z4a5"
branch_labels = None
depends_on = None

JOB_STATUS_VALUES = (
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "ABORTED",
    "UNKNOWN",
)
PLAN_RUN_STATUS_VALUES = (
    "RUNNING",
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "DEGRADED",
)


def _job_status_enum(native: bool):
    return sa.Enum(
        *JOB_STATUS_VALUES,
        name="job_status",
        native_enum=native,
        validate_strings=True,
    )


def _plan_run_status_enum(native: bool):
    return sa.Enum(
        *PLAN_RUN_STATUS_VALUES,
        name="plan_run_status",
        native_enum=native,
        validate_strings=True,
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    has_plan_run = "plan_run" in tables
    job_columns = {
        column["name"] for column in inspector.get_columns("job_instance")
    }

    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS uq_job_active_per_device")

        job_status_enum = postgresql.ENUM(*JOB_STATUS_VALUES, name="job_status")
        plan_run_status_enum = postgresql.ENUM(
            *PLAN_RUN_STATUS_VALUES, name="plan_run_status"
        )
        job_status_enum.create(bind, checkfirst=True)
        plan_run_status_enum.create(bind, checkfirst=True)

        op.execute("ALTER TABLE job_instance ALTER COLUMN status DROP DEFAULT")
        op.execute(
            "ALTER TABLE job_instance "
            "ALTER COLUMN status TYPE job_status USING status::job_status"
        )
        op.execute("ALTER TABLE job_instance ALTER COLUMN status SET DEFAULT 'PENDING'")

        # This revision is on a branch parallel to ADR-0020.  A fresh Alembic
        # database reaches it before x1y2z3a4b5c6 creates plan_run; the merge
        # revision finishes this conversion after both branches converge.
        if has_plan_run:
            op.execute("ALTER TABLE plan_run ALTER COLUMN status DROP DEFAULT")
            op.execute(
                "ALTER TABLE plan_run "
                "ALTER COLUMN status TYPE plan_run_status USING status::plan_run_status"
            )
            op.execute("ALTER TABLE plan_run ALTER COLUMN status SET DEFAULT 'RUNNING'")
    else:
        with op.batch_alter_table("job_instance") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.String(length=32),
                type_=_job_status_enum(native=False),
                existing_nullable=False,
                server_default="PENDING",
            )
        if has_plan_run:
            with op.batch_alter_table("plan_run") as batch_op:
                batch_op.alter_column(
                    "status",
                    existing_type=sa.String(length=32),
                    type_=_plan_run_status_enum(native=False),
                    existing_nullable=False,
                    server_default="RUNNING",
                )

    if "plan_run_id" in job_columns:
        op.create_index(
            "idx_job_instance_plan_run_status",
            "job_instance",
            ["plan_run_id", "status"],
        )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_job_active_per_device
            ON job_instance (device_id)
         WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.execute("DROP INDEX IF EXISTS uq_job_active_per_device")
    op.drop_index("idx_job_instance_plan_run_status", table_name="job_instance")

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE job_instance ALTER COLUMN status DROP DEFAULT")
        op.execute(
            "ALTER TABLE job_instance "
            "ALTER COLUMN status TYPE VARCHAR(32) USING status::text"
        )
        op.execute("ALTER TABLE job_instance ALTER COLUMN status SET DEFAULT 'PENDING'")

        op.execute("ALTER TABLE plan_run ALTER COLUMN status DROP DEFAULT")
        op.execute(
            "ALTER TABLE plan_run "
            "ALTER COLUMN status TYPE VARCHAR(32) USING status::text"
        )
        op.execute("ALTER TABLE plan_run ALTER COLUMN status SET DEFAULT 'RUNNING'")

        postgresql.ENUM(name="job_status").drop(bind, checkfirst=True)
        postgresql.ENUM(name="plan_run_status").drop(bind, checkfirst=True)
    else:
        with op.batch_alter_table("job_instance") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=_job_status_enum(native=False),
                type_=sa.String(length=32),
                existing_nullable=False,
                server_default="PENDING",
            )
        with op.batch_alter_table("plan_run") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=_plan_run_status_enum(native=False),
                type_=sa.String(length=32),
                existing_nullable=False,
                server_default="RUNNING",
            )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_job_active_per_device
            ON job_instance (device_id)
         WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')
        """
    )
