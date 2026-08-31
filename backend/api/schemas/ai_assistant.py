# -*- coding: utf-8 -*-
"""AI 助手 API schemas（ADR-0031）。api_key 永不回明文——只回掩码。"""

from datetime import datetime

from pydantic import Field

from backend.api.schemas.base import ORMBaseModel


class AiAssistantConfigOut(ORMBaseModel):
    model_config = ORMBaseModel.model_config.copy()
    model_config["from_attributes"] = True

    base_url: str = ""
    model: str = ""
    api_key_masked: str | None = None
    enabled: bool = False
    temperature: float = 0.2
    max_turns: int = 8
    request_timeout_seconds: int = 120
    t1_require_confirm: bool = False
    auto_approve_tools: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class AiAssistantConfigUpdate(ORMBaseModel):
    base_url: str | None = None
    model: str | None = None
    # 留空/缺省 = 不变更（不上送字段即不变）
    api_key: str | None = None
    enabled: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_turns: int | None = Field(default=None, ge=1, le=20)
    request_timeout_seconds: int | None = Field(default=None, ge=10, le=600)
    t1_require_confirm: bool | None = None
    auto_approve_tools: list[str] | None = None


class AiConnectionTestOut(ORMBaseModel):
    ok: bool
    latency_ms: int | None = None
    model: str = ""
    error: str | None = None


class AiSessionOut(ORMBaseModel):
    id: int
    title: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AiMessageOut(ORMBaseModel):
    id: int
    session_id: int
    role: str
    content: str = ""
    tool_calls: list = Field(default_factory=list)
    tool_call_id: str | None = None
    status: str = "completed"
    meta: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class AiActionOut(ORMBaseModel):
    id: int
    session_id: int
    tool_name: str
    params: dict = Field(default_factory=dict)
    status: str
    console_run_id: str | None = None
    result_summary: str | None = None
    preview_text: str | None = None
    requested_by: str | None = None
    decided_by: str | None = None
    created_at: datetime | None = None
    decided_at: datetime | None = None
