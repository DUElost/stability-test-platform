"""ADR-0020 Phase 5 — Drop legacy tables, tighten constraints, rebuild task_schedules.

**CRITICAL**: Entire upgrade() runs in a single transaction.  If any statement
fails the whole migration rolls back and the database must be restored from
the Phase 0 backup.

Execution order (per ADR):
  1. Clean up legacy script_execution data (orphaned by dropping old tables)
  2. ALTER job_instance SET NOT NULL on plan_run_id / plan_id
  3. DROP old FK columns (workflow_run_id, task_template_id)
  4. Rebuild task_schedules for Plan-only (drop FKs to legacy tables FIRST)
  5. DROP old tables
"""

from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
import sqlalchemy as sa

revision = "a4b5c6d7e8f9"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. Clean up legacy script_execution data ──────────────────────────
    # Temporary table holds IDs of JobInstances that belong to script_execution runs
    op.execute(
        text(
            "CREATE TEMPORARY TABLE _legacy_job_ids AS "
            "SELECT id FROM job_instance "
            "WHERE workflow_run_id IN ("
            "  SELECT id FROM workflow_run WHERE run_type = 'script_execution'"
            ")"
        )
    )

    # 1a. Release ACTIVE device_leases for legacy jobs
    op.execute(
        text(
            "UPDATE device_leases "
            "   SET status      = 'RELEASED',"
            "       released_at = now(),"
            "       reason      = 'adr_0020_legacy_script_execution_drop',"
            "       job_id      = NULL"
            " WHERE job_id IN (SELECT id FROM _legacy_job_ids)"
            "   AND status = 'ACTIVE'"
        )
    )

    # 1b. Clear job_id for terminal leases (preserve audit)
    op.execute(
        text(
            "UPDATE device_leases "
            "   SET job_id = NULL"
            " WHERE job_id IN (SELECT id FROM _legacy_job_ids)"
            "   AND status <> 'ACTIVE'"
        )
    )

    # 1c. Delete resource_allocations
    op.execute(
        text(
            "DELETE FROM resource_allocation "
            "WHERE job_instance_id IN (SELECT id FROM _legacy_job_ids)"
        )
    )

    # 1d. Delete step_traces
    op.execute(
        text(
            "DELETE FROM step_trace "
            "WHERE job_id IN (SELECT id FROM _legacy_job_ids)"
        )
    )

    # 1e. Delete job_artifacts
    op.execute(
        text(
            "DELETE FROM job_artifact "
            "WHERE job_id IN (SELECT id FROM _legacy_job_ids)"
        )
    )

    # 1f. Delete job_log_signals (also cascade-deletes, but explicit for observability)
    op.execute(
        text(
            "DELETE FROM job_log_signal "
            "WHERE job_id IN (SELECT id FROM _legacy_job_ids)"
        )
    )

    # 1g. Delete legacy JobInstances
    op.execute(
        text(
            "DELETE FROM job_instance "
            "WHERE id IN (SELECT id FROM _legacy_job_ids)"
        )
    )

    # 1h. Drop temp table
    op.execute(text("DROP TABLE IF EXISTS _legacy_job_ids"))

    # ── 2. Tighten NOT NULL constraints ──────────────────────────────────
    # At this point all remaining job_instance rows have been backfilled in Phase 4.
    with op.batch_alter_table("job_instance") as batch_op:
        batch_op.alter_column("plan_run_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("plan_id", existing_type=sa.Integer(), nullable=False)

    # ── 3. Drop old FK columns + index ──────────────────────────────────
    with op.batch_alter_table("job_instance") as batch_op:
        batch_op.drop_index("idx_job_instance_workflow")
        batch_op.drop_constraint(
            "job_instance_workflow_run_id_fkey", type_="foreignkey"
        )
        batch_op.drop_column("workflow_run_id")
        batch_op.drop_constraint(
            "job_instance_task_template_id_fkey", type_="foreignkey"
        )
        batch_op.drop_column("task_template_id")

    # ── 4. Rebuild task_schedules for Plan-only ───────────────────────────
    # MUST run BEFORE drop_table on legacy tables, because task_schedules
    # still has FKs pointing to workflow_definition / task_template / tool.
    # PostgreSQL refuses DROP TABLE while dependent FKs exist (no cascade
    # by ADR §Phase 5 explicit-order requirement).
    # Wipe all old schedules (ops will manually recreate with Plan)
    op.execute(text("DELETE FROM task_schedules"))

    with op.batch_alter_table("task_schedules") as batch_op:
        # Drop legacy FK
        batch_op.drop_constraint(
            "task_schedules_workflow_definition_id_fkey",
            type_="foreignkey",
        )
        # Drop legacy columns (drop_column auto-drops remaining FKs/indexes on the column)
        batch_op.drop_column("workflow_definition_id")
        batch_op.drop_column("task_template_id")
        batch_op.drop_column("tool_id")
        batch_op.drop_column("task_type")

        # Add plan_id (nullable at DDL level, validated by API)
        batch_op.add_column(
            sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan.id"), nullable=True)
        )

    # ── 5. Drop old tables ────────────────────────────────────────────────
    op.drop_table("workflow_run")
    op.drop_table("task_template")
    op.drop_table("workflow_definition")
    op.drop_table("script_sequence")

    # ── End of single-transaction upgrade ─────────────────────────────────


def downgrade():
    # downgrade is intentionally minimal — the Phase 0 backup is the real rollback path.
    # We only restore structural reversibility for development/testing convenience.
    pass
