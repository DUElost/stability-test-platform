# -*- coding: utf-8 -*-
"""test_suite.exported_global_sha256：Global 文件磁盘漂移基线（R05-F10 / #973）

原先仅 runtask.xml 有 exported_sha256；Global（UiAutomatorTestData.xml）磁盘
丢失/被改无检测。新增同构基线列，导出时记录 Global 渲染文件 sha，门禁据其
校验磁盘对象。

Revision ID: bb22cc33dd44
Revises: aa11bb22cc33
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa

revision = "bb22cc33dd44"
down_revision = "aa11bb22cc33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_suite",
        sa.Column("exported_global_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_suite", "exported_global_sha256")
