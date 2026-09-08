"""add host.maintenance_until / maintenance_holder — 主机维护窗口（#960）

Revision ID: m8n9o0p1q2r3
Revises: n4o5p6q7r8s9
Create Date: 2026-09-08

热更新此前只在发起前做一次活跃 Job 检查（409 / abort drain / 前端预检）；
检查结束后到上传、rsync、重启之间是**无互斥的窗口**——期间仍可新派发或
claim 到该主机，重启会打断刚派下去的作业。

host 增加两个可空列表达维护窗口：

- ``maintenance_until TIMESTAMPTZ NULL``：窗口截止时刻（NULL = 无窗口）；
- ``maintenance_holder VARCHAR(128) NULL``：持有者标识（并发热更新互相识别）。

用「截止时刻」而非布尔标志：持有进程崩溃时不会把主机永久钉在维护态，
窗口到点自然失效，不需要额外的清扫任务。两列均可空，ADD COLUMN 为元数据级
变更，不重写存量行。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m8n9o0p1q2r3"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column("maintenance_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column("maintenance_holder", sa.String(128), nullable=True, default=""),
    )


def downgrade() -> None:
    op.drop_column("host", "maintenance_holder")
    op.drop_column("host", "maintenance_until")
