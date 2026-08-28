# -*- coding: utf-8 -*-
"""平台 AI 助手实体（ADR-0031）。

四表：配置单行表 / 会话 / 消息 / T1+T2 动作审批流。
api_key 只存 Fernet 密文（core/ai_security.py），任何序列化路径不得回明文。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.core.database import Base


class AiAssistantConfig(Base):
    """平台级运行时配置（单行表，id 恒为 1）。"""

    __tablename__ = "ai_assistant_config"

    id = Column(Integer, primary_key=True, default=1)
    base_url = Column(String(512), nullable=False, default="", server_default="")
    model = Column(String(128), nullable=False, default="", server_default="")
    api_key_encrypted = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    temperature = Column(Float, nullable=False, default=0.2, server_default="0.2")
    max_turns = Column(Integer, nullable=False, default=8, server_default="8")
    request_timeout_seconds = Column(Integer, nullable=False, default=120, server_default="120")
    # T1 收回开关：true = 测试门禁类工具也走审批
    t1_require_confirm = Column(Boolean, nullable=False, default=False, server_default="false")
    # 免确认白名单：仅 T2 级低危工具可加入（后端校验）
    auto_approve_tools = Column(JSONB, nullable=False, default=list, server_default="[]")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AiChatSession(Base):
    __tablename__ = "ai_chat_session"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="", server_default="")
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AiChatMessage(Base):
    __tablename__ = "ai_chat_message"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("ai_chat_session.id"), nullable=False, index=True)
    # user | assistant | tool | system
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False, default="", server_default="")
    # assistant 消息的 tool_calls 数组：[{id, name, arguments}]
    tool_calls = Column(JSONB, nullable=False, default=list, server_default="[]")
    tool_call_id = Column(String(64), nullable=True)
    # pending | running | completed | failed
    status = Column(String(16), nullable=False, default="completed", server_default="completed")
    # usage / latency_ms / error / proposed_action_id
    meta = Column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class AiAssistantAction(Base):
    """T1（收回/自动批准后）与 T2 动作的统一审批流实体。"""

    __tablename__ = "ai_assistant_action"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("ai_chat_session.id"), nullable=False, index=True)
    tool_name = Column(String(64), nullable=False)
    params = Column(JSONB, nullable=False, default=dict, server_default="{}")
    # proposed | approved | rejected | expired | running | succeeded | failed | cancelled
    status = Column(String(16), nullable=False, default="proposed", server_default="proposed", index=True)
    console_run_id = Column(String(64), nullable=True)
    result_summary = Column(Text, nullable=True)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    decided_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)
