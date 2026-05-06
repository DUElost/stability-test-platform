"""Repair databases stamped past ADR-0020 before schema alignment.

Some local databases reached revision b5c6d7e8f9a0 while still retaining a few
pre-final ADR-0020 columns.  This migration is intentionally idempotent so it
can run safely after the consolidated ADR-0020 migrations on fresh databases.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def _table_names(inspector):
    return set(inspector.get_table_names())


def _columns(inspector, table):
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(inspector, table):
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _table_names(inspector)

    if "audit_logs" not in tables:
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("username", sa.String(128), nullable=True),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("resource_type", sa.String(64), nullable=False),
            sa.Column("resource_id", sa.Integer(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column("ip_address", sa.String(64), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_audit_user_ts", "audit_logs", ["user_id", "timestamp"])
        op.create_index("ix_audit_resource", "audit_logs", ["resource_type", "resource_id"])
    else:
        idx = _indexes(inspector, "audit_logs")
        if "ix_audit_user_ts" not in idx:
            op.create_index("ix_audit_user_ts", "audit_logs", ["user_id", "timestamp"])
        if "ix_audit_resource" not in idx:
            op.create_index("ix_audit_resource", "audit_logs", ["resource_type", "resource_id"])

    if "plan" in tables:
        cols = _columns(inspector, "plan")
        if "timeout_seconds" not in cols:
            op.add_column("plan", sa.Column("timeout_seconds", sa.Integer(), nullable=True))
        if "lifecycle" in cols:
            op.drop_column("plan", "lifecycle")

    if "plan_migration_audit" in tables:
        cols = _columns(inspector, "plan_migration_audit")
        if "old_workflow_run_id" not in cols:
            op.add_column(
                "plan_migration_audit",
                sa.Column("old_workflow_run_id", sa.Integer(), nullable=True),
            )
        if "new_plan_run_id" not in cols:
            op.add_column(
                "plan_migration_audit",
                sa.Column("new_plan_run_id", sa.Integer(), nullable=True),
            )

    if "task_schedules" in tables:
        cols = _columns(inspector, "task_schedules")
        legacy_cols = {
            "workflow_definition_id",
            "task_template_id",
            "tool_id",
            "task_type",
            "params",
            "target_device_id",
        }
        if legacy_cols.intersection(cols) or "plan_id" not in cols:
            op.execute(text("DELETE FROM task_schedules"))

        cols = _columns(sa.inspect(bind), "task_schedules")
        if "plan_id" not in cols:
            op.add_column("task_schedules", sa.Column("plan_id", sa.Integer(), nullable=True))
            op.create_foreign_key(
                "task_schedules_plan_id_fkey",
                "task_schedules",
                "plan",
                ["plan_id"],
                ["id"],
            )

        for col in (
            "workflow_definition_id",
            "task_template_id",
            "tool_id",
            "task_type",
            "params",
            "target_device_id",
        ):
            if col in cols:
                op.drop_column("task_schedules", col)

        op.alter_column(
            "task_schedules",
            "plan_id",
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade():
    pass

