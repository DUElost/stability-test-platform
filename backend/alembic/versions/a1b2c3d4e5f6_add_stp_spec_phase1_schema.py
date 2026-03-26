"""add stp spec phase1 schema

Revision ID: a1b2c3d4e5f6
Revises: c1a2b3d4e5f6
Create Date: 2026-02-27

Phase 1 (CREATE-only): Create new STP schema tables alongside existing
legacy tables.  Legacy tables are NOT dropped here — that happens in a
future migration after all code and data have been migrated.

Rewritten on 2026-03-26 as part of ADR-0008 Wave 4 (incremental strategy).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "c1a2b3d4e5f6"
branch_labels = None
depends_on = None


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' AND table_name = :tbl"
            ")"
        ),
        {"tbl": table_name},
    )
    return result.scalar()


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "tool"):
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

    if not _table_exists(conn, "host"):
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

    if not _table_exists(conn, "device"):
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

    if not _table_exists(conn, "workflow_definition"):
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

    if not _table_exists(conn, "task_template"):
        op.create_table(
            "task_template",
            sa.Column("id",                      sa.Integer(), primary_key=True),
            sa.Column("workflow_definition_id",  sa.Integer(), sa.ForeignKey("workflow_definition.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name",                    sa.String(256), nullable=False),
            sa.Column("pipeline_def",            postgresql.JSONB(), nullable=False),
            sa.Column("platform_filter",         postgresql.JSONB()),
            sa.Column("sort_order",              sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at",              sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if not _table_exists(conn, "workflow_run"):
        op.create_table(
            "workflow_run",
            sa.Column("id",                      sa.Integer(),  primary_key=True),
            sa.Column("workflow_definition_id",  sa.Integer(),  sa.ForeignKey("workflow_definition.id"), nullable=False),
            sa.Column("status",                  sa.String(32), nullable=False, server_default="RUNNING"),
            sa.Column("failure_threshold",       sa.Float(),    nullable=False, server_default="0.05"),
            sa.Column("triggered_by",            sa.String(128)),
            sa.Column("started_at",              sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("ended_at",                sa.DateTime(timezone=True)),
            sa.Column("result_summary",          postgresql.JSONB()),
        )

    if not _table_exists(conn, "job_instance"):
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

    if not _table_exists(conn, "step_trace"):
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


def downgrade() -> None:
    conn = op.get_bind()
    for table in ("step_trace", "job_instance", "workflow_run", "task_template",
                  "workflow_definition", "device", "host", "tool"):
        if _table_exists(conn, table):
            op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
