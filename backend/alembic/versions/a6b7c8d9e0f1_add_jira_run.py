"""add jira_run table (ADR-0025 §10 历史记录)

Revision ID: a6b7c8d9e0f1
Revises: t5u6v7w8x9y0
Create Date: 2026-07-01

JiraRun 持久化每次「去重→Jira 一键执行」run：vendor/stage/dry-run/reporter/
输入来源/终态/issue_keys。RunConsole 是内存态单例重启即丢，本表补齐持久化层，
供前端「历史记录」Tab 查询与日志 replay。issue_keys 由 on_complete 回调从
RunConsole 落盘日志解析（厂商脚本 stdout 中形如 STABILITY-123 的 key）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "t5u6v7w8x9y0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jira_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("console_run_id", sa.String(64), nullable=False),
        sa.Column("vendor", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reporter", sa.String(128), nullable=True),
        sa.Column("input_source", sa.String(512), nullable=False, server_default="upload"),
        sa.Column("plan_run_id", sa.Integer(), sa.ForeignKey("plan_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("artifact_id", sa.Integer(), sa.ForeignKey("plan_run_artifact.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="RUNNING"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("issue_keys", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("error", sa.String(1024), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("console_run_id", name="uq_jira_run_console_run_id"),
    )
    op.create_index("ix_jira_run_console_run_id", "jira_run", ["console_run_id"], unique=True)
    op.create_index("idx_jira_run_created", "jira_run", ["created_at"])
    op.create_index("idx_jira_run_vendor_status", "jira_run", ["vendor", "status"])


def downgrade() -> None:
    op.drop_index("idx_jira_run_vendor_status", table_name="jira_run")
    op.drop_index("idx_jira_run_created", table_name="jira_run")
    op.drop_index("ix_jira_run_console_run_id", table_name="jira_run")
    op.drop_table("jira_run")