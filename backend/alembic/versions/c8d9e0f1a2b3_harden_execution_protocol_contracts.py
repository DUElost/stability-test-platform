"""Harden PlanRun/Job execution protocol contracts.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM job_instance
             GROUP BY plan_run_id, device_id
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'cannot add uq_job_instance_plan_run_device: duplicate rows exist';
          END IF;
        END
        $$;
        """
    )

    op.add_column(
        "job_instance",
        sa.Column("terminal_payload_digest", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_job_instance_plan_run_device",
        "job_instance",
        ["plan_run_id", "device_id"],
    )

    op.create_check_constraint(
        "ck_plan_failure_threshold",
        "plan",
        "failure_threshold >= 0.0 AND failure_threshold <= 1.0",
    )
    op.create_check_constraint(
        "ck_plan_run_failure_threshold",
        "plan_run",
        "failure_threshold >= 0.0 AND failure_threshold <= 1.0",
    )

    op.add_column(
        "step_trace",
        sa.Column("trace_event_id", sa.String(length=256), nullable=True),
    )
    op.execute(
        """
        UPDATE step_trace
           SET trace_event_id =
               'legacy:' || job_id::text || ':' || step_id || ':' || event_type
         WHERE trace_event_id IS NULL
        """
    )
    op.alter_column("step_trace", "trace_event_id", nullable=False)
    op.drop_constraint(
        "uq_step_trace_idempotent",
        "step_trace",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_step_trace_event_id",
        "step_trace",
        ["trace_event_id"],
    )

    # Defensive: ORM declares uq_job_active_per_device; ensure DB matches even if
    # an older environment skipped m1g2h3i4j5k6.  Preflight scans active duplicates.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT device_id
              FROM job_instance
             WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')
             GROUP BY device_id
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'cannot ensure uq_job_active_per_device: duplicate active jobs per device';
          END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_job_active_per_device
            ON job_instance (device_id)
         WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_job_active_per_device")
    op.drop_constraint(
        "uq_step_trace_event_id",
        "step_trace",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_step_trace_idempotent",
        "step_trace",
        ["job_id", "step_id", "event_type"],
    )
    op.drop_column("step_trace", "trace_event_id")

    op.drop_constraint(
        "ck_plan_run_failure_threshold",
        "plan_run",
        type_="check",
    )
    op.drop_constraint(
        "ck_plan_failure_threshold",
        "plan",
        type_="check",
    )
    op.drop_constraint(
        "uq_job_instance_plan_run_device",
        "job_instance",
        type_="unique",
    )
    op.drop_column("job_instance", "terminal_payload_digest")
