# -*- coding: utf-8 -*-
"""平台 AI 助手四表（ADR-0031 D3）

Revision ID: q9r8s7t6u5v4
Revises: i5j6k7l8m9n0
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "q9r8s7t6u5v4"
down_revision = "i5j6k7l8m9n0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_assistant_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("t1_require_confirm", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "auto_approve_tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "ai_chat_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_chat_session_user_id", "ai_chat_session", ["user_id"])

    op.create_table(
        "ai_chat_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("ai_chat_session.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("tool_call_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_chat_message_session_id", "ai_chat_message", ["session_id"])

    op.create_table(
        "ai_assistant_action",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("ai_chat_session.id"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="proposed"
        ),
        sa.Column("console_run_id", sa.String(length=64), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column(
            "requested_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_assistant_action_session", "ai_assistant_action", ["session_id"])
    op.create_index("ix_ai_assistant_action_status", "ai_assistant_action", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ai_assistant_action_status", table_name="ai_assistant_action")
    op.drop_index("ix_ai_chat_message_action_session", table_name="ai_assistant_action")
    op.drop_table("ai_assistant_action")
    op.drop_index("ix_ai_chat_message_session_id", table_name="ai_chat_message")
    op.drop_table("ai_chat_message")
    op.drop_index("ix_ai_chat_session_user_id", table_name="ai_chat_session")
    op.drop_table("ai_chat_session")
    op.drop_table("ai_assistant_config")
