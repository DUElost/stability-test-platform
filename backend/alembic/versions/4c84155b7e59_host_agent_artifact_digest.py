"""add host.agent_artifact_digest — 部署摘要协议状态载体（ADR-0040 D2，#1907）

Revision ID: 4c84155b7e59
Revises: f3a4b5c6d7e8
Create Date: 2026-09-13

ADR-0040 把「部署单元」升级为内容寻址 artifact：远端 current digest 由部署
流程受控写入 ``$INSTALL_DIR/agent/ARTIFACT_DIGEST``，Agent 经心跳上报，控制面
落 Host **显式列**（禁 ``Host.extra`` 裸键，ADR-0038 D4 先例；对照
``host.script_catalog_version`` 同型）。desired digest 由控制面现算，不落
host 表（desired 是控制面 artifact 的属性，不是 host 的属性）。

additive nullable、无回填（存量主机 digest 为空 → P1 gate 视为未收敛，下次
热更新全量部署并由远端脚本写入 digest，一次迁移后进入 no-op 稳态）；
ADD COLUMN 属元数据级变更，不重写存量行，不新增索引。

downgrade drop 该列 = 丢失 current digest 记录，所有主机回到「未收敛」态，
下一次热更新全量重做（不丢数据、只丢优化）。CI 不跑 downgrade，仅在本地/
一次性容器往返验证。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "4c84155b7e59"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column("agent_artifact_digest", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("host", "agent_artifact_digest")
