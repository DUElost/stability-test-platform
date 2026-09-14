"""add host.agent_resources_digest（ADR-0040 P2 身份分层显式列，#1963）

Revision ID: i6j7k8l9m0n1
Revises: 4c84155b7e59
Create Date: 2026-09-14

host 新增显式列 ``agent_resources_digest``（String(80)）——P2 分层扩展的
host-resources 身份载体：部署流程收敛成功后受控写入远端
``ARTIFACT_DIGEST_RESOURCES``，Agent 经心跳上报（与
``agent_artifact_digest`` 同通道、同信任模型；ADR-0038 D4 先例——宿主状态
走显式列）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "i6j7k8l9m0n1"
down_revision = "4c84155b7e59"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column("agent_resources_digest", sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("host", "agent_resources_digest")
