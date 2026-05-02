"""Drop script_run and script_batch tables (ADR-0019 Phase 6e).

ScriptBatch was converged into script_execution (Phase 5). The ORM model and
service were deleted in Phase 6a. These tables are no longer accessed.

Revision ID: v0w1x2y3z4a5
Revises:    u9v0w1x2y3z4
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "v0w1x2y3z4a5"
down_revision = "u9v0w1x2y3z4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_script_run_batch", table_name="script_run")
    op.drop_table("script_run")
    op.drop_index("idx_script_batch_host", table_name="script_batch")
    op.drop_index("idx_script_batch_device", table_name="script_batch")
    op.drop_index("idx_script_batch_status", table_name="script_batch")
    op.drop_table("script_batch")


def downgrade() -> None:
    op.create_table(
        "script_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("sequence_id", sa.Integer(), sa.ForeignKey("script_sequence.id"), nullable=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("device.id"), nullable=False),
        sa.Column("host_id", sa.String(64), sa.ForeignKey("host.id"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("on_failure", sa.String(16), nullable=False, server_default="stop"),
        sa.Column("log_dir", sa.String(512), nullable=True),
        sa.Column("watcher_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watcher_stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watcher_capability", sa.String(32), nullable=True),
        sa.Column("log_signal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_script_batch_status", "script_batch", ["status"])
    op.create_index("idx_script_batch_device", "script_batch", ["device_id"])
    op.create_index("idx_script_batch_host", "script_batch", ["host_id"])

    op.create_table(
        "script_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("script_batch.id"), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("script_name", sa.String(128), nullable=False),
        sa.Column("script_version", sa.String(32), nullable=False),
        sa.Column("params_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_script_run_batch", "script_run", ["batch_id"])
