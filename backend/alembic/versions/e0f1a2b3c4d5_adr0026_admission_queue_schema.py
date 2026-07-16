"""ADR-0026 P1 step 1 — additive admission-queue schema (schema only).

Adds, without changing any existing dispatch behavior:
- plan_run: admission-queue columns (priority / queue_reason / next_admission_at
  / admission_token / admission_attempt_id / precheck_started_at / enqueued_at)
  + five O(1) aggregation counters
- plan_run_status enum: QUEUED / PRECHECK values (additive; nothing produces
  them until the feature-flag path lands — ADR-0026 落地顺序 P1 step 2+)
- plan_run_host: per-host projection of a PlanRun (prepare-time immutable
  snapshot; coordinator fields enabled after admission)
- plan_run_target_device: prepare-time relational snapshot of target devices
  (replaces run_context.dispatch_device_ids JSON as the authoritative list)
- job_instance: execution_state + last_execution_heartbeat_at +
  last_progress_at (invariant ③ — three independent liveness signals)

All new columns are nullable or carry a safe server_default, per the accepted
rollout plan (schema-only first; state machine + feature flag follow).

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa


revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── plan_run_status enum: add QUEUED / PRECHECK ────────────────────────
    # ALTER TYPE ... ADD VALUE cannot run inside the migration transaction on
    # older PG (and the new value could not be referenced later in the same
    # transaction anyway) — use an autocommit block. IF NOT EXISTS keeps the
    # migration re-runnable against a DB where create_all already added them.
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE plan_run_status ADD VALUE IF NOT EXISTS 'QUEUED'")
            op.execute("ALTER TYPE plan_run_status ADD VALUE IF NOT EXISTS 'PRECHECK'")

    # ── plan_run: admission-queue columns + O(1) counters ──────────────────
    op.add_column("plan_run", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("plan_run", sa.Column("queue_reason", sa.String(32), nullable=True))
    op.add_column("plan_run", sa.Column("next_admission_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plan_run", sa.Column("admission_token", sa.String(64), nullable=True))
    op.add_column("plan_run", sa.Column("admission_attempt_id", sa.String(64), nullable=True))
    op.add_column("plan_run", sa.Column("precheck_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plan_run", sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plan_run", sa.Column("total_job_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("plan_run", sa.Column("terminal_job_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("plan_run", sa.Column("completed_job_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("plan_run", sa.Column("failed_job_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("plan_run", sa.Column("aborted_job_count", sa.Integer(), nullable=False, server_default="0"))

    # Pump scan index: partial on QUEUED (few rows), covering the dequeue
    # ordering columns. Sort-direction tuning is deferred (ADR「索引考量」).
    if bind.dialect.name == "postgresql":
        op.create_index(
            "idx_plan_run_admission_queue",
            "plan_run",
            ["priority", "next_admission_at", "enqueued_at"],
            postgresql_where=sa.text("status = 'QUEUED'"),
        )

    # ── plan_run_host ───────────────────────────────────────────────────────
    op.create_table(
        "plan_run_host",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), sa.ForeignKey("plan_run.id"), nullable=False),
        sa.Column("host_id", sa.String(64), sa.ForeignKey("host.id"), nullable=False),
        sa.Column("device_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_ADMISSION"),
        sa.Column("phase", sa.String(32), nullable=True),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coordinator_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coordinator_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admission_batch_size_snapshot", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("queue_reason", sa.String(32), nullable=True),
        sa.Column("total_job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("terminal_job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aborted_job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("plan_run_id", "host_id", name="uq_plan_run_host"),
    )
    op.create_index("idx_plan_run_host_host_phase", "plan_run_host", ["host_id", "phase"])

    # ── plan_run_target_device ──────────────────────────────────────────────
    op.create_table(
        "plan_run_target_device",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), sa.ForeignKey("plan_run.id"), nullable=False),
        sa.Column("plan_run_host_id", sa.Integer(), sa.ForeignKey("plan_run_host.id"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("device.id"), nullable=False),
        sa.Column("host_id_snapshot", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("plan_run_id", "device_id", name="uq_plan_run_target_device"),
    )
    op.create_index("idx_prtd_device", "plan_run_target_device", ["device_id"])
    op.create_index("idx_prtd_plan_run_host", "plan_run_target_device", ["plan_run_host_id"])

    # ── job_instance: three independent liveness signals (invariant ③) ─────
    op.add_column("job_instance", sa.Column("execution_state", sa.String(32), nullable=True))
    op.add_column("job_instance", sa.Column("last_execution_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_instance", sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_column("job_instance", "last_progress_at")
    op.drop_column("job_instance", "last_execution_heartbeat_at")
    op.drop_column("job_instance", "execution_state")

    op.drop_index("idx_prtd_plan_run_host", table_name="plan_run_target_device")
    op.drop_index("idx_prtd_device", table_name="plan_run_target_device")
    op.drop_table("plan_run_target_device")

    op.drop_index("idx_plan_run_host_host_phase", table_name="plan_run_host")
    op.drop_table("plan_run_host")

    if bind.dialect.name == "postgresql":
        op.drop_index("idx_plan_run_admission_queue", table_name="plan_run")

    for col in (
        "aborted_job_count", "failed_job_count", "completed_job_count",
        "terminal_job_count", "total_job_count", "enqueued_at",
        "precheck_started_at", "admission_attempt_id", "admission_token",
        "next_admission_at", "queue_reason", "priority",
    ):
        op.drop_column("plan_run", col)

    # PG enum values are intentionally NOT removed: ALTER TYPE has no
    # DROP VALUE, and rebuilding plan_run_status would rewrite plan_run.
    # QUEUED/PRECHECK stay as harmless orphan values (nothing produces them
    # once the columns above are gone).
