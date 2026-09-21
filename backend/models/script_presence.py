"""host × 脚本版本 的在位矩阵（#2958 第五道闸的落地面）。

背景：run 作用域的 host 侧核验（`admission_pump._verify_scripts_phase`）只在派发时覆盖
「本 run 的 host × 本 run 快照」，维护窗 / 近期无 run 的 host 无账（#2958：`.89` 缺 3 个
版本目录而 DB 面全绿）。本表把**常设 sweep** 的结果落下来，供指标（抓取期现算）与
只读 API 消费。

一行 = 一台 host × 一个目标版本的**当前**态（每轮 sweep 全量 upsert + 刷新 `checked_at`
与 `sweep_id`）。`state` 是闭词表（见 `backend/services/script_presence.py`）：

- ``present``      sha256 匹配（在位）
- ``missing``      文件缺失/不可读（agent 侧 ``file_missing_or_unreadable``）
- ``mismatch``     内容不符（``sha_mismatch`` / support 文件不符）
- ``unknown``      agent 不可达（**未知不是绿**，与 `agent_offline` 口径一致）
- ``n_a``          全集有、该 host 的可达集没有（不判红）
- ``maintenance``  维护窗内 host 的缺口单列（不判红，归队前补分发由流程盯）
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from backend.core.database import Base


class HostScriptPresence(Base):
    __tablename__ = "host_script_presence"

    id = Column(Integer, primary_key=True)
    host_id = Column(
        String(64),
        ForeignKey("host.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    #: 脚本族名（与 `script.name` 同口径，非 FK：版本可退役，账本要留痕）
    name = Column(String(128), nullable=False)
    version = Column(String(32), nullable=False)
    #: 五态闭词表 + maintenance（见模块 docstring）
    state = Column(String(16), nullable=False)
    #: 诊断码（agent 侧 error 原文或本侧判定码），空串 = 无
    detail = Column(String(256), nullable=False, default="", server_default="")
    #: 本轮 sweep 的观测时刻（每轮刷新；min(checked_at) 即「最近一次完整 sweep」）
    checked_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    #: 轮次标识（UUID hex），同轮写入的行共享它——UI/排障可判「同一张快照」
    sweep_id = Column(String(32), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("host_id", "name", "version", name="uq_host_script_presence_key"),
        Index("idx_host_script_presence_state", "state"),
        Index("idx_host_script_presence_checked", "checked_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 排障用
        return (
            f"<HostScriptPresence {self.host_id} {self.name}@{self.version}"
            f" {self.state}>"
        )
