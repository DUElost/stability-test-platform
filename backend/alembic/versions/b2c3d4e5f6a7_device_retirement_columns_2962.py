"""add device retirement columns — 设备退役生命周期（ADR-0057 D1/D3，#2962）

Revision ID: b2c3d4e5f6a7
Revises: f7a8b9c0d1e2
Create Date: 2026-09-27

ADR-0057 把「设备退役」（人确认的终态：报废/归还/不再使用）与 `status`
（心跳上报的存活）分家：`status` 的 owner 是 Agent 心跳，复用它会静默撤销
运维决定；且 202 OFFLINE 里 188 台是数周未见的沉积（#2962 实测），容量口径、
链选与 OFFLINE 指标都被污染。故生命周期单独用一组可空列表达：

- ``retired_at TIMESTAMPTZ NULL``：退役时刻，**非空即退役**，NULL = 在役；
- ``retired_by VARCHAR(128) NULL``：执行退役的操作者（审计 who）；
- ``retire_reason TEXT NULL``：退役原因（D2 要求必填，列为 Text 不设长度上限）；
- ``retire_alerted_at TIMESTAMPTZ NULL``：D3「已退役但仍在心跳」单次告警的
  去重载体（与前三列同批落地，心跳端点消费）。

全部 additive nullable、**无回填**（存量设备默认在役），ADD COLUMN 属元数据级
变更，不重写存量行；按 ADR §4「本次不新增索引」（索引另案）。

``downgrade`` 会 **drop 四列 = 丢失全部退役状态**（谁、何时、为何退役），且
已退役设备将重新回到派发/容量集合。CI 不跑 downgrade，只在本地/一次性容器
往返验证存在性与可逆性；生产回滚需先评估该语义损失。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "device",
        sa.Column("retired_by", sa.String(128), nullable=True),
    )
    op.add_column(
        "device",
        sa.Column("retire_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "device",
        sa.Column("retire_alerted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # 顺序与 upgrade 相反；四列均无依赖索引/约束（ADR §4 不新增索引）。
    op.drop_column("device", "retire_alerted_at")
    op.drop_column("device", "retire_reason")
    op.drop_column("device", "retired_by")
    op.drop_column("device", "retired_at")
