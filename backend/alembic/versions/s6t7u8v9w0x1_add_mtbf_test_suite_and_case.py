"""ADR-0030 P1a — test_suite / test_case 建表（配置层实体）。

DDL only，无回填（既有 130 条用例经 `POST /test-suites/{id}/import` 入库）。
两个快照列分工见 P1 设计 §2 总则：`exported_content_sha256` 检测库漂移、
`exported_sha256` 检测磁盘漂移——都是门禁**计算**比对，不靠端点置空。

Revision ID: s6t7u8v9w0x1
Revises:    r5s6t7u8v9w0
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "s6t7u8v9w0x1"
down_revision = "r5s6t7u8v9w0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_suite",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("export_dir", sa.String(128), nullable=True),
        sa.Column("apk_binding", postgresql.JSONB(), nullable=True),
        # root_config / global_params / exec_descs 用 JSON 而非 JSONB：
        # JSONB 重排对象键，导出物就不再与源文件逐字节同构（见 models 注释）。
        sa.Column("root_config", sa.JSON(), nullable=False,
                  server_default="{}"),
        sa.Column("global_params", sa.JSON(), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("exported_sha256", sa.String(64), nullable=True),
        sa.Column("exported_content_sha256", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["test_project.id"],
                                name="fk_test_suite_project"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_test_suite_name"),
    )
    op.create_index("idx_test_suite_project", "test_suite", ["project_id"])

    op.create_table(
        "test_case",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=False),
        # testpoint name 实测最长 ~80 字符（中文），512 留足余量
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("times", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("exec_descs", sa.JSON(), nullable=False,
                  server_default="[]"),
        sa.ForeignKeyConstraint(["suite_id"], ["test_suite.id"],
                                ondelete="CASCADE",
                                name="fk_test_case_suite"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suite_id", "name", name="uq_test_case_suite_name"),
    )
    op.create_index("idx_test_case_suite_ordinal", "test_case",
                    ["suite_id", "ordinal"])


def downgrade() -> None:
    op.drop_index("idx_test_case_suite_ordinal", table_name="test_case")
    op.drop_table("test_case")
    op.drop_index("idx_test_suite_project", table_name="test_suite")
    op.drop_table("test_suite")
