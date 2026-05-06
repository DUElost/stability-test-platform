"""ADR-0020 Phase 2 — Create plan / plan_step / plan_run / plan_migration_audit tables,
add plan_run_id / plan_id (nullable) to job_instance, and add default_params to script.

DDL only; data migration is in the next two revisions.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "x1y2z3a4b5c6"
down_revision = "w0x1y2z3a4b5"
branch_labels = None
depends_on = None


def upgrade():
    # ── plan ───────────────────────────────────────────────────────────
    op.create_table(
        "plan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("failure_threshold", sa.Float(), nullable=False,
                  server_default="0.05"),
        sa.Column("patrol_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("next_plan_id", sa.Integer(), nullable=True),
        sa.Column("watcher_policy", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "next_plan_id IS NULL OR next_plan_id <> id",
            name="ck_plan_no_self_chain",
        ),
        sa.ForeignKeyConstraint(["next_plan_id"], ["plan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_plan_next_plan", "plan", ["next_plan_id"])

    # ── plan_step ──────────────────────────────────────────────────────
    op.create_table(
        "plan_step",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("step_key", sa.String(256), nullable=False),
        sa.Column("script_name", sa.String(128), nullable=False),
        sa.Column("script_version", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("retry", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "stage IN ('init', 'patrol', 'teardown')",
            name="ck_plan_step_stage",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plan.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "step_key",
                            name="uq_plan_step_key"),
    )
    op.create_index("idx_plan_step_plan_stage_order", "plan_step",
                    ["plan_id", "stage", "sort_order"])

    # ── plan_run ───────────────────────────────────────────────────────
    op.create_table(
        "plan_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="RUNNING"),
        sa.Column("failure_threshold", sa.Float(), nullable=False,
                  server_default="0.05"),
        sa.Column("plan_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("run_type", sa.String(16), nullable=False),
        sa.Column("run_context", postgresql.JSONB(), nullable=True),
        sa.Column("triggered_by", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(), nullable=True),
        sa.Column("parent_plan_run_id", sa.Integer(), nullable=True),
        sa.Column("root_plan_run_id", sa.Integer(), nullable=True),
        sa.Column("chain_index", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("next_plan_triggered", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.CheckConstraint(
            "run_type IN ('MANUAL','SCHEDULE','CHAIN')",
            name="ck_plan_run_type",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"]),
        sa.ForeignKeyConstraint(["parent_plan_run_id"], ["plan_run.id"]),
        sa.ForeignKeyConstraint(["root_plan_run_id"], ["plan_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_plan_run_plan", "plan_run", ["plan_id"])
    op.create_index("idx_plan_run_status", "plan_run", ["status"])
    op.create_index("idx_plan_run_parent", "plan_run",
                    ["parent_plan_run_id"])
    op.create_index("idx_plan_run_root", "plan_run",
                    ["root_plan_run_id"])
    op.create_index(
        "uniq_plan_run_chain_child",
        "plan_run",
        ["parent_plan_run_id", "plan_id"],
        unique=True,
        postgresql_where=sa.text("parent_plan_run_id IS NOT NULL"),
    )

    # ── plan_migration_audit ───────────────────────────────────────────
    op.create_table(
        "plan_migration_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("old_workflow_definition_id", sa.Integer(), nullable=True),
        sa.Column("old_task_template_id", sa.Integer(), nullable=True),
        sa.Column("old_workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("new_plan_id", sa.Integer(), nullable=False),
        sa.Column("new_plan_run_id", sa.Integer(), nullable=True),
        sa.Column("chain_index", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── job_instance: nullable plan_run_id / plan_id; relax legacy FKs ──
    op.add_column("job_instance",
                  sa.Column("plan_run_id", sa.Integer(), nullable=True))
    op.add_column("job_instance",
                  sa.Column("plan_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_job_instance_plan_run", "job_instance",
                          "plan_run", ["plan_run_id"], ["id"])
    op.create_foreign_key("fk_job_instance_plan", "job_instance",
                          "plan", ["plan_id"], ["id"])

    # Allow Plan-dispatched JobInstances to have NULL legacy FKs during
    # the transition window (Phase 5 will drop them entirely).
    op.alter_column("job_instance", "workflow_run_id", nullable=True)
    op.alter_column("job_instance", "task_template_id", nullable=True)

    # ── script: default_params ─────────────────────────────────────────
    op.add_column("script",
                  sa.Column("default_params", postgresql.JSONB(),
                            nullable=False, server_default="{}"))

    # Backfill existing rows with empty object
    op.execute(sa.text(
        "UPDATE script SET default_params = '{}'::jsonb"
    ))


def downgrade():
    op.drop_column("script", "default_params")

    op.alter_column("job_instance", "task_template_id", nullable=False)
    op.alter_column("job_instance", "workflow_run_id", nullable=False)

    op.drop_constraint("fk_job_instance_plan", "job_instance",
                       type_="foreignkey")
    op.drop_constraint("fk_job_instance_plan_run", "job_instance",
                       type_="foreignkey")
    op.drop_column("job_instance", "plan_id")
    op.drop_column("job_instance", "plan_run_id")

    op.drop_table("plan_migration_audit")

    op.drop_index("uniq_plan_run_chain_child", "plan_run")
    op.drop_index("idx_plan_run_root", "plan_run")
    op.drop_index("idx_plan_run_parent", "plan_run")
    op.drop_index("idx_plan_run_status", "plan_run")
    op.drop_index("idx_plan_run_plan", "plan_run")
    op.drop_table("plan_run")

    op.drop_index("idx_plan_step_plan_stage_order", "plan_step")
    op.drop_table("plan_step")

    op.drop_index("idx_plan_next_plan", "plan")
    op.drop_table("plan")
