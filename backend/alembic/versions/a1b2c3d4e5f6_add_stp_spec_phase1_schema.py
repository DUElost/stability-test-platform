"""add stp spec phase1 schema

Revision ID: a1b2c3d4e5f6
Revises: c1a2b3d4e5f6
Create Date: 2026-02-27

Phase 1: Drop all legacy tables, create new stp-spec schema.
This migration is irreversible (downgrade raises NotImplementedError).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "c1a2b3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. New tables (dependency order) ──────────────────────────────────────

    op.create_table(
        "tool",
        sa.Column("id",           sa.Integer(),      primary_key=True),
        sa.Column("name",         sa.String(128),    nullable=False),
        sa.Column("version",      sa.String(32),     nullable=False),
        sa.Column("script_path",  sa.Text(),         nullable=False),
        sa.Column("script_class", sa.String(128),    nullable=False),
        sa.Column("param_schema", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active",    sa.Boolean(),      nullable=False, server_default=sa.text("true")),
        sa.Column("description",  sa.Text()),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", "version", name="uq_tool_name_version"),
    )

    op.create_table(
        "host",
        sa.Column("id",                   sa.String(64),  primary_key=True),
        sa.Column("hostname",             sa.String(256), nullable=False),
        sa.Column("ip_address",           sa.String(64)),
        sa.Column("tool_catalog_version", sa.String(64)),
        sa.Column("last_heartbeat",       sa.DateTime(timezone=True)),
        sa.Column("cpu_quota",            sa.Integer(),   nullable=False, server_default="2"),
        sa.Column("status",               sa.String(32),  nullable=False, server_default="OFFLINE"),
        sa.Column("created_at",           sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_host_last_heartbeat", "host", ["last_heartbeat"])

    op.create_table(
        "device",
        sa.Column("id",         sa.Integer(),   primary_key=True),
        sa.Column("serial",     sa.String(128), nullable=False, unique=True),
        sa.Column("host_id",    sa.String(64),  sa.ForeignKey("host.id")),
        sa.Column("model",      sa.String(128)),
        sa.Column("platform",   sa.String(64)),
        sa.Column("tags",       postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status",     sa.String(32),  nullable=False, server_default="OFFLINE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_device_host", "device", ["host_id"])

    op.create_table(
        "workflow_definition",
        sa.Column("id",                sa.Integer(),     primary_key=True),
        sa.Column("name",              sa.String(256),   nullable=False),
        sa.Column("description",       sa.Text()),
        sa.Column("failure_threshold", sa.Float(),       nullable=False, server_default="0.05"),
        sa.Column("created_by",        sa.String(128)),
        sa.Column("created_at",        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "task_template",
        sa.Column("id",                     sa.Integer(), primary_key=True),
        sa.Column("workflow_definition_id", sa.Integer(), sa.ForeignKey("workflow_definition.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",                   sa.String(256), nullable=False),
        sa.Column("pipeline_def",     postgresql.JSONB(), nullable=False),
        sa.Column("platform_filter",  postgresql.JSONB()),
        sa.Column("sort_order",             sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at",             sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "workflow_run",
        sa.Column("id",                     sa.Integer(),  primary_key=True),
        sa.Column("workflow_definition_id", sa.Integer(),  sa.ForeignKey("workflow_definition.id"), nullable=False),
        sa.Column("status",                 sa.String(32), nullable=False, server_default="RUNNING"),
        sa.Column("failure_threshold",      sa.Float(),    nullable=False, server_default="0.05"),
        sa.Column("triggered_by",           sa.String(128)),
        sa.Column("started_at",             sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at",               sa.DateTime(timezone=True)),
        sa.Column("result_summary",         postgresql.JSONB()),
    )

    op.create_table(
        "job_instance",
        sa.Column("id",               sa.Integer(),   primary_key=True),
        sa.Column("workflow_run_id",  sa.Integer(),   sa.ForeignKey("workflow_run.id"),  nullable=False),
        sa.Column("task_template_id", sa.Integer(),   sa.ForeignKey("task_template.id"), nullable=False),
        sa.Column("device_id",        sa.Integer(),   sa.ForeignKey("device.id"),        nullable=False),
        sa.Column("host_id",          sa.String(64),  sa.ForeignKey("host.id")),
        sa.Column("status",           sa.String(32),  nullable=False, server_default="PENDING"),
        sa.Column("status_reason",    sa.Text()),
        sa.Column("pipeline_def",     postgresql.JSONB(), nullable=False),
        sa.Column("started_at",       sa.DateTime(timezone=True)),
        sa.Column("ended_at",         sa.DateTime(timezone=True)),
        sa.Column("created_at",       sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",       sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_job_instance_status",   "job_instance", ["status"])
    op.create_index("idx_job_instance_workflow",  "job_instance", ["workflow_run_id"])
    op.create_index("idx_job_instance_host",      "job_instance", ["host_id"])

    op.create_table(
        "step_trace",
        sa.Column("id",            sa.Integer(),   primary_key=True),
        sa.Column("job_id",        sa.Integer(),   sa.ForeignKey("job_instance.id"), nullable=False),
        sa.Column("step_id",       sa.String(128), nullable=False),
        sa.Column("stage",         sa.String(32),  nullable=False),
        sa.Column("status",        sa.String(32),  nullable=False),
        sa.Column("event_type",    sa.String(32),  nullable=False),
        sa.Column("output",        sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("original_ts",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "step_id", "event_type", name="uq_step_trace_idempotent"),
    )
    op.create_index("idx_step_trace_job", "step_trace", ["job_id"])

    # ── 2. Drop legacy tables (reverse dependency order) ──────────────────────

    # run_steps depends on task_runs
    op.execute("DROP TABLE IF EXISTS run_steps CASCADE")
    # task_runs depends on tasks, hosts, devices
    op.execute("DROP TABLE IF EXISTS task_runs CASCADE")
    # workflow_steps depends on workflows
    op.execute("DROP TABLE IF EXISTS workflow_steps CASCADE")
    # remaining leaf tables
    op.execute("DROP TABLE IF EXISTS task_templates CASCADE")
    op.execute("DROP TABLE IF EXISTS tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS workflows CASCADE")
    op.execute("DROP TABLE IF EXISTS log_artifacts CASCADE")
    op.execute("DROP TABLE IF EXISTS deployments CASCADE")
    op.execute("DROP TABLE IF EXISTS device_metric_snapshots CASCADE")
    # tool_categories must drop before tools (FK)
    op.execute("DROP TABLE IF EXISTS tool_categories CASCADE")
    op.execute("DROP TABLE IF EXISTS tools CASCADE")
    # devices / hosts (old integer-PK tables)
    op.execute("DROP TABLE IF EXISTS devices CASCADE")
    op.execute("DROP TABLE IF EXISTS hosts CASCADE")
    # other legacy tables
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS schedules CASCADE")
    op.execute("DROP TABLE IF EXISTS notifications CASCADE")
    op.execute("DROP TABLE IF EXISTS task_schedules CASCADE")


def downgrade() -> None:
    raise NotImplementedError("Phase 1 migration is irreversible (historical data discarded)")
