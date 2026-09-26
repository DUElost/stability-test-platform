"""add host.emptied_at / emptied_by / emptied_reason — 设备面意图位（#3159，ADR-0038 v0.3 D9.1）

Revision ID: f7a8b9c0d1e2
Revises: d4e8f2a7c9b1
Create Date: 2026-09-26

「人为清空 / 移机 / 关机」的 host 在平台账上仍有设备行，设备面告警
（StabilityHostUsbBlind / StabilityHostAdbOfflineConcentration）critical 常亮
（#3065 现网 5 台）。D9 裁决：意图由人显式声明（可审计、可逆），规则侧按声明豁免。

host 增加三个可空列承载意图，真源只在此三列（禁 Host.extra 裸键——主心跳每拍
重建 extra，ADR-0038 D4 同款约束）：

- ``emptied_at TIMESTAMPTZ NULL``：置位时刻（NULL = 无意图）；
- ``emptied_by VARCHAR(128) NULL``：置位人（交付面取 username，对齐 retired_by）；
- ``emptied_reason TEXT NULL``：置位原因（置位时必填，清除后保留最近一次）。

与 ``retired_at`` 互斥（D9.2）：互斥由服务层 409 保证，迁移层只加列。
三列均可空，ADD COLUMN 为元数据级变更，不重写存量行。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "d4e8f2a7c9b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column("emptied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column("emptied_by", sa.String(128), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column("emptied_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("host", "emptied_reason")
    op.drop_column("host", "emptied_by")
    op.drop_column("host", "emptied_at")
