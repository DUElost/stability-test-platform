"""add jira_run.jira_project_key (G17 登记簿映射闭环)

Revision ID: c17d8e9f20a1
Revises: f0a1b2c3d4e5
Create Date: 2026-08-27

jira_project_key：source=plan_run 的提单 run 经 PlanRun→Plan→test_project
解析出的目标 Jira 项目键，逐 run 审计。上传源（upload）run 无项目上下文，
恒 NULL；缺失键不阻断提单（厂商工具 config 默认映射仍生效）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c17d8e9f20a1"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jira_run",
        sa.Column("jira_project_key", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jira_run", "jira_project_key")
