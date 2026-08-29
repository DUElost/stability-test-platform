# -*- coding: utf-8 -*-
"""纠正 ai_assistant_action 上的索引名（低危：建表时张冠李戴带 chat 前缀）

Revision ID: r0s9t8u7v6w5
Revises: q9r8s7t6u5v4
Create Date: 2026-08-29
"""

from alembic import op

revision = "r0s9t8u7v6w5"
down_revision = "q9r8s7t6u5v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS 兼容两条路径：存量库改名；新库已由 q9r8 直接建对新名
    op.execute(
        "ALTER INDEX IF EXISTS ix_ai_chat_message_action_session "
        "RENAME TO ix_ai_assistant_action_session"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX IF EXISTS ix_ai_assistant_action_session "
        "RENAME TO ix_ai_chat_message_action_session"
    )
