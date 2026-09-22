"""host 脚本在位矩阵的响应模型（#2958 第五道闸）。

形状是**闭词表五态 + 维护态**（`services.script_presence.PRESENCE_STATES`）：
``present / missing / mismatch / unknown / n_a / maintenance``。前端 `types.ts` 有同形
声明，两侧由 `tests/test_api_response_shape_contract.py` 的 `_MODEL_PAIRS` 对拍
（轴线 C：Pydantic 注解字段 ↔ TS 字段集，双向包含）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ScriptPresenceItem(BaseModel):
    """单个「host × 目标版本」的当前态。"""

    name: str
    version: str
    state: str = Field(description="present/missing/mismatch/unknown/n_a/maintenance")
    detail: str = ""


class ScriptPresenceCounts(BaseModel):
    """按态计数（六态恒全，缺为 0——PromQL/前端都不需要再补基线）。"""

    present: int = 0
    missing: int = 0
    mismatch: int = 0
    unknown: int = 0
    n_a: int = 0
    maintenance: int = 0


class HostScriptPresenceOut(BaseModel):
    """单台 host 的矩阵（`GET /script-presence/hosts/{host_id}`）。"""

    host_id: str
    checked_at: Optional[datetime] = None
    sweep_id: str = ""
    counts: ScriptPresenceCounts
    items: list[ScriptPresenceItem] = Field(default_factory=list)


class ScriptPresenceSummaryOut(BaseModel):
    """fleet 汇总（`GET /script-presence/summary`）。"""

    counts: ScriptPresenceCounts
    hosts_total: int = 0
    hosts_with_gap: int = 0
    full_versions: int = 0
    uncovered_active_versions: int = Field(
        default=0,
        ge=0,
        description=(
            "已 active 但**无任何 Plan 引用**的版本数（#3111）：这些版本进不了任何主机的"
            "可达集，账本一行都不写。读汇总时 `counts.missing/mismatch = 0` **只**覆盖"
            " `full_versions`，不含它们——新合并、尚无 Plan 引用的脚本版本正落在这个盲区，"
            "其到位情况看 `agent_code_sync_status` 与一次 fleet 热更新"
        ),
    )
    checked_at_min: Optional[datetime] = None
    checked_at_max: Optional[datetime] = None
    stale: bool = Field(
        default=False,
        description="最近一次完整 sweep 超过 2×周期（按 24h 判）：账本不新鲜时不得当绿读",
    )


class ScriptPresenceSweepOut(BaseModel):
    """单机按需刷新（`POST /script-presence/refresh`）的返回。"""

    sweep_id: str
    host_id: str
    rows: int = 0
    counts: ScriptPresenceCounts
