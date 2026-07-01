"""JiraRun — 批量提单运行记录（ADR-0025 §10 持久化层）。

RunConsole 是进程级内存态单例，重启即丢。本表把每次「去重→Jira 一键执行」
的关键信息持久化：who/when/vendor/stage/dry-run/reporter/输入来源/终态/issue_keys，
供前端「历史记录」Tab 查询与日志 replay。

issue_keys 由 on_complete 回调从 RunConsole 落盘的日志文件解析得出
（厂商脚本 stdout 中形如 STABILITY-123 的 issue key）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from backend.core.database import Base

# SQLite（测试）无 JSONB，回落到通用 JSON
_JsonType = JSONB().with_variant(JSON(), "sqlite")


class JiraRun(Base):
    """单次批量提单 run 的持久化记录。console_run_id 关联 RunConsole 内存态。"""

    __tablename__ = "jira_run"

    id                  = Column(Integer, primary_key=True)
    console_run_id      = Column(String(64), nullable=False, unique=True, index=True)
    vendor              = Column(String(32), nullable=False)
    stage               = Column(String(32), nullable=False)  # upload_list | create
    dry_run             = Column(Boolean, nullable=False, default=True)
    reporter            = Column(String(128), nullable=True)
    # 输入来源：upload=手动上传（input_source 存原文件名）/ artifact=PlanRun 产物（存 storage_uri）
    input_source        = Column(String(512), nullable=False, default="upload")
    plan_run_id         = Column(Integer, ForeignKey("plan_run.id", ondelete="SET NULL"), nullable=True)
    artifact_id         = Column(Integer, ForeignKey("plan_run_artifact.id", ondelete="SET NULL"), nullable=True)
    status              = Column(String(16), nullable=False, default="RUNNING")  # RUNNING|SUCCESS|FAILED|CANCELED
    started_at          = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at            = Column(DateTime(timezone=True), nullable=True)
    exit_code           = Column(Integer, nullable=True)
    issue_keys          = Column(_JsonType, nullable=False, default=list)
    error               = Column(String(1024), nullable=True)
    created_by_user_id  = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at          = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_jira_run_created", "created_at"),
        Index("idx_jira_run_vendor_status", "vendor", "status"),
    )