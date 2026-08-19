"""ADR-0029 P1 M-a — test_project / specialty 建表 + 归属列（全部 nullable）。

DDL only，无回填、无读路径（M-b/M-c 回填在 tools/dev/backfill-project-mc.py）。
v2 最小形态：不含 variables（D4 挂起）/ storage_key（D7 挂起）。

Revision ID: r5s6t7u8v9w0
Revises:    q4r5s6t7u8v9
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa

revision = "r5s6t7u8v9w0"
down_revision = "q4r5s6t7u8v9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── test_project（登记簿：身份单层 + 正交 facet）────────────────────
    op.create_table(
        "test_project",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_key", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("jira_project_key", sa.String(32), nullable=True),
        sa.Column("product_line", sa.String(64), nullable=True),
        sa.Column("customer", sa.String(64), nullable=True),
        sa.Column("platform", sa.String(64), nullable=True),
        sa.Column("form_factor", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')",
                           name="ck_test_project_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_key", name="uq_test_project_key"),
    )

    # ── specialty 字典表（D6，Plan 列表二维分组用）────────────────────
    op.create_table(
        "specialty",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_specialty_key"),
    )
    op.create_index("idx_specialty_sort", "specialty", ["sort_order"])

    # ── 归属列（全部 nullable：迁移期瞬态，M-b/M-c 回填后归零）─────────
    op.add_column(
        "plan",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("test_project.id"),
                  nullable=True),
    )
    op.add_column(
        "plan",
        sa.Column("specialty_id", sa.Integer(), sa.ForeignKey("specialty.id"),
                  nullable=True),
    )
    op.create_index("idx_plan_project", "plan", ["project_id"])

    op.add_column(
        "device",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("test_project.id"),
                  nullable=True),
    )
    op.create_index("idx_device_project", "device", ["project_id"])

    op.add_column(
        "plan_run",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("test_project.id"),
                  nullable=True),
    )
    op.add_column(
        "plan_run",
        sa.Column("build_version", sa.String(256), nullable=True),
    )
    op.create_index("idx_plan_run_project", "plan_run", ["project_id"])


def downgrade() -> None:
    op.drop_index("idx_plan_run_project", table_name="plan_run")
    op.drop_column("plan_run", "build_version")
    op.drop_column("plan_run", "project_id")
    op.drop_index("idx_device_project", table_name="device")
    op.drop_column("device", "project_id")
    op.drop_index("idx_plan_project", table_name="plan")
    op.drop_column("plan", "specialty_id")
    op.drop_column("plan", "project_id")
    op.drop_index("idx_specialty_sort", table_name="specialty")
    op.drop_table("specialty")
    op.drop_table("test_project")
