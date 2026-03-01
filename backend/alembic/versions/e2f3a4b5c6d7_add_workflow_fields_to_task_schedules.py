"""add workflow fields to task_schedules

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-02-28

Phase E-3: Recreate task_schedules (dropped by a1b2c3d4e5f6 Phase-1) with
workflow_definition_id and device_ids columns.

a1b2c3d4e5f6 dropped the old task_schedules table. This migration recreates
it for the cron-scheduler, referencing the new-schema table names:
  - workflow_definition (not workflow_definitions)
  - legacy FKs to task_templates / tools / devices removed (those tables gone)
"""

from alembic import op
import sqlalchemy as sa

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "task_schedules" in existing:
        # Table already exists (e.g. partial rollback): just ensure new cols.
        cols = {c["name"] for c in inspector.get_columns("task_schedules")}
        if "workflow_definition_id" not in cols:
            op.add_column("task_schedules", sa.Column(
                "workflow_definition_id", sa.Integer(),
                sa.ForeignKey("workflow_definition.id"),
                nullable=True,
            ))
        if "device_ids" not in cols:
            op.add_column("task_schedules", sa.Column(
                "device_ids", sa.JSON(), nullable=True,
            ))
        return

    # Normal path: recreate the table dropped by Phase-1 migration.
    op.create_table(
        "task_schedules",
        sa.Column("id",                    sa.Integer(),     primary_key=True),
        sa.Column("name",                  sa.String(128),   nullable=False),
        sa.Column("cron_expression",       sa.String(128),   nullable=False),
        # Legacy FK cols kept as plain integers (target tables dropped in Phase-1)
        sa.Column("task_template_id",      sa.Integer(),     nullable=True),
        sa.Column("tool_id",               sa.Integer(),     nullable=True),
        sa.Column("task_type",             sa.String(32),    nullable=False, server_default="MONKEY"),
        sa.Column("params",                sa.JSON(),        nullable=True,  server_default="{}"),
        sa.Column("target_device_id",      sa.Integer(),     nullable=True),
        sa.Column("enabled",               sa.Boolean(),     nullable=False, server_default="true"),
        sa.Column("last_run_at",           sa.DateTime(),    nullable=True),
        sa.Column("next_run_at",           sa.DateTime(),    nullable=True),
        sa.Column("created_by",            sa.Integer(),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at",            sa.DateTime(),    nullable=False,
                  server_default=sa.func.now()),
        # New Phase-E cols
        sa.Column("workflow_definition_id", sa.Integer(),
                  sa.ForeignKey("workflow_definition.id"), nullable=True),
        sa.Column("device_ids",            sa.JSON(),        nullable=True),
    )
    op.create_index(
        "ix_sched_enabled_next", "task_schedules", ["enabled", "next_run_at"]
    )


def downgrade() -> None:
    op.drop_table("task_schedules")
