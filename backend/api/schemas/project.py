"""ADR-0029 P2 — TestProject 对外 schema。

口径（F2）：对外一律 ``project_key``（URL / API / 日志 / 审计全链路），
数字 id 只留 DB 外键——本模块不暴露 ``id``。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from backend.api.schemas.base import ORMBaseModel


class ProjectOut(ORMBaseModel):
    project_key: str
    display_name: str
    jira_project_key: Optional[str] = None
    product_line: Optional[str] = None
    customer: Optional[str] = None
    platform: Optional[str] = None
    form_factor: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectSummaryOut(ProjectOut):
    """列表卡片：项目行 + 设备数 / 在跑 Run 数聚合。"""

    device_count: int = 0
    running_run_count: int = 0


class RecentProjectRunOut(ORMBaseModel):
    """详情页「最近结果」块的轻量行（明细列表走 plan-runs 接口带 project_key）。"""

    id: int
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None


class ProjectDetailOut(ProjectSummaryOut):
    """详情聚合：计数 + 最近 Run；设备 / Plan / Run 明细由前端带
    ``project_key`` 调对应列表接口（本项目不重复提供明细列表）。"""

    plan_count: int = 0
    total_run_count: int = 0
    recent_runs: List[RecentProjectRunOut] = []


# ── ADR-0029 P2.5a：Fleet 事实（只读聚合）────────────────────────────────


class InventoryModelOut(ORMBaseModel):
    """一种 ``device.model`` 的 fleet 聚合。

    ``backfill_project_keys`` 是 P1 脚本灌入的 ``device.project_id`` 标签
    （HONOR-MLD 等），**不是**客户 / 项目 / 机型，也不是人工映射。
    ``mapped_project_keys`` 才是人工填写的映射；P2.5a 无规则表，恒为 ``[]``。
    """

    model: Optional[str] = None
    device_count: int
    platforms: List[str]
    backfill_project_keys: List[str]
    mapped_project_keys: List[str] = []
    legacy_device_count: int = 0
    null_device_count: int = 0


class InventorySummaryOut(ORMBaseModel):
    """工作台顶栏：全 fleet 计数 + 完全未编入真实回填标签的 model 列表。"""

    total_devices: int
    mapped_devices: int
    legacy_devices: int
    null_devices: int
    distinct_models: int
    unmapped_models: List[Optional[str]]


class ProjectModelCoverageOut(ORMBaseModel):
    """某回填标签当前挂着的型号（GROUP BY device.model；非正式映射）。"""

    model: Optional[str] = None
    device_count: int
    platforms: List[str]
