"""add host retirement columns — 主机退役生命周期（ADR-0038 D1/D4，#1800）

Revision ID: f3a4b5c6d7e8
Revises: a3b2c1d0e9f8
Create Date: 2026-09-13

ADR-0038 把「退役」（不再使用，历史终态）与 status（心跳上报的存活）分家：
status 的 owner 是 Agent 心跳，若复用 status=RETIRED，下一次心跳就会把运维
决定静默撤销。故生命周期单独用一组可空列表达：

- ``retired_at TIMESTAMPTZ NULL``：退役时刻，**非空即退役**，NULL = 在用；
- ``retired_by VARCHAR(128) NULL``：执行退役的操作者（审计 who）；
- ``retire_reason TEXT NULL``：退役原因（ADR D2 要求必填，列为 Text 不设长度上限）；
- ``retire_alerted_at TIMESTAMPTZ NULL``：D4「已退役但仍在心跳」单次告警的
  去重载体（与 D1 三列同批落地，② 的 retire / ④ 的心跳告警消费）。

全部 additive nullable、**无回填**（存量主机默认在用），ADD COLUMN 属元数据级
变更，不重写存量行；按 ADR §4「本次不新增索引」（drop 列连带删索引是 #644
事故形态，索引另案）。

``downgrade`` 会 **drop 四列 = 丢失全部退役状态**（谁、何时、为何退役），且
退役主机将重新回到可派发集合。CI 不跑 downgrade（ADR §4），只在本地/一次性
容器往返验证存在性与可逆性；生产回滚需先评估该语义损失。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a4b5c6d7e8"
down_revision = "a3b2c1d0e9f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column("retired_by", sa.String(128), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column("retire_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column("retire_alerted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # 顺序与 upgrade 相反；四列均无依赖索引/约束（ADR §4 不新增索引）。
    op.drop_column("host", "retire_alerted_at")
    op.drop_column("host", "retire_reason")
    op.drop_column("host", "retired_by")
    op.drop_column("host", "retired_at")
