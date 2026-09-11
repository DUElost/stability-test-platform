# -*- coding: utf-8 -*-
"""AI 助手自动续轮累计预算（R13-R01 / #1227）

- ai_assistant_config.max_auto_continuations：单条自动执行链的累计续轮上限（可配置）
- ai_chat_session.auto_continuation_count：自最近一次用户消息以来的自动续轮计数

Revision ID: aa11bb22cc33
Revises: w4x3y2z1a0b9
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa

revision = "aa11bb22cc33"
down_revision = "w4x3y2z1a0b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_assistant_config",
        sa.Column(
            "max_auto_continuations",
            sa.Integer(),
            nullable=False,
            server_default="20",
        ),
    )
    op.add_column(
        "ai_chat_session",
        sa.Column(
            "auto_continuation_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_session", "auto_continuation_count")
    op.drop_column("ai_assistant_config", "max_auto_continuations")
