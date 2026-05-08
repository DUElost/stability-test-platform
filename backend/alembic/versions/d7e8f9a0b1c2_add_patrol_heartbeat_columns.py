"""ADR-0022: Add patrol heartbeat columns to job_instance.

Adds 8 columns to support the patrol heartbeat aggregation model:
  - patrol_cycle_count          : total cycles executed
  - patrol_success_cycle_count  : cycles where all steps succeeded
  - patrol_failed_cycle_count   : cycles with at least one step failure
  - current_patrol_step         : currently executing step name (for UI)
  - last_patrol_heartbeat_at    : last heartbeat timestamp (liveness)
  - current_failure_streak      : consecutive failure count for backoff
  - next_retry_at               : next patrol retry time (NULL = no backoff)
  - manual_action               : NULL / RETRY_NOW / EXIT_REQUESTED

Plus an index on (plan_run_id, last_patrol_heartbeat_at) for stall queries
and matrix aggregation.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-05-08
"""

from alembic import op
import sqlalchemy as sa


revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


_NEW_COLUMNS = (
    ("patrol_cycle_count",         sa.Integer(),                       "0"),
    ("patrol_success_cycle_count", sa.Integer(),                       "0"),
    ("patrol_failed_cycle_count",  sa.Integer(),                       "0"),
    ("current_patrol_step",        sa.Text(),                          None),
    ("last_patrol_heartbeat_at",   sa.DateTime(timezone=True),         None),
    ("current_failure_streak",     sa.Integer(),                       "0"),
    ("next_retry_at",              sa.DateTime(timezone=True),         None),
    ("manual_action",              sa.String(length=32),               None),
)


def _columns(inspector, table):
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(inspector, table):
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "job_instance" not in set(inspector.get_table_names()):
        # 主仓库迁移链中 job_instance 必然存在；保留兜底防御。
        return

    existing = _columns(inspector, "job_instance")

    for name, col_type, server_default in _NEW_COLUMNS:
        if name in existing:
            continue
        kwargs = {"nullable": True}
        if server_default is not None:
            kwargs["server_default"] = sa.text(server_default)
        op.add_column("job_instance", sa.Column(name, col_type, **kwargs))

    # Counter columns are NOT NULL with default 0 once back-filled
    op.execute(
        "UPDATE job_instance "
        "   SET patrol_cycle_count         = COALESCE(patrol_cycle_count, 0), "
        "       patrol_success_cycle_count = COALESCE(patrol_success_cycle_count, 0), "
        "       patrol_failed_cycle_count  = COALESCE(patrol_failed_cycle_count, 0), "
        "       current_failure_streak     = COALESCE(current_failure_streak, 0)"
    )
    for col in (
        "patrol_cycle_count",
        "patrol_success_cycle_count",
        "patrol_failed_cycle_count",
        "current_failure_streak",
    ):
        op.alter_column("job_instance", col, nullable=False, existing_type=sa.Integer())

    # Index for stall detection + per-PlanRun device matrix aggregation
    indexes = _indexes(inspector, "job_instance")
    if "idx_job_instance_patrol_heartbeat" not in indexes:
        op.create_index(
            "idx_job_instance_patrol_heartbeat",
            "job_instance",
            ["plan_run_id", "last_patrol_heartbeat_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "job_instance" not in set(inspector.get_table_names()):
        return

    indexes = _indexes(inspector, "job_instance")
    if "idx_job_instance_patrol_heartbeat" in indexes:
        op.drop_index("idx_job_instance_patrol_heartbeat", table_name="job_instance")

    existing = _columns(inspector, "job_instance")
    for name, _col_type, _default in reversed(_NEW_COLUMNS):
        if name in existing:
            op.drop_column("job_instance", name)
