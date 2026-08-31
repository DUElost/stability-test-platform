"""ADR-0029 P2 — TestProject 对外 schema。

口径（F2）：对外一律 ``project_key``（URL / API / 日志 / 审计全链路），
数字 id 只留 DB 外键——本模块不暴露 ``id``。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from backend.api.schemas.base import ORMBaseModel

_PROJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")

class ProjectOut(ORMBaseModel):
    project_key: str
    display_name: str
    jira_project_key: Optional[str] = None
    customer: Optional[str] = None
    # ADR-0029 v2.5 D12：platforms 派生（设备侧）；product_line/form_factor
    # 已删（判据：能改变某人的行为才值得存在——生产 5 行里 2 行 product_line
    # 是 customer 副本、1 行字符串 'None'；form_factor 除一个 TABLET 全 PHONE）
    platforms: List[str] = []
    status: str
    source: str = "USER"
    match_models: List[str] = []
    created_at: datetime
    updated_at: datetime


class ProjectSummaryOut(ProjectOut):
    """列表卡片：项目行 + 设备数 / 在跑 Run 数聚合。"""

    device_count: int = 0
    running_run_count: int = 0


class SpecialtyOut(ORMBaseModel):
    """D6 专项字典行——Plan 编辑器下拉与列表分组用（#405 接线）。"""

    key: str
    display_name: str
    sort_order: int


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


class ProjectCreateIn(BaseModel):
    project_key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    customer: Optional[str] = None
    jira_project_key: Optional[str] = None

    @field_validator("project_key")
    @classmethod
    def normalize_project_key(cls, value: str) -> str:
        key = value.strip()
        if not _PROJECT_KEY_RE.match(key):
            raise ValueError("project_key must match [A-Za-z0-9][A-Za-z0-9-]{0,62}")
        return key

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("display_name must not be blank")
        return name

    @field_validator("jira_project_key")
    @classmethod
    def validate_jira_project_key(cls, value: Optional[str]) -> Optional[str]:
        """v2.5 D12：宽松校验——只挡空白与超长（生产值含中文/连字符，
        规范校验留详情页「未验证」标记与提单前探测）。"""
        if value is None:
            return None
        key = value.strip()
        if not key:
            return None
        if len(key) > 32:
            raise ValueError("jira_project_key must be at most 32 chars")
        if any(ch.isspace() for ch in key):
            raise ValueError("jira_project_key must not contain whitespace")
        return key


class ProjectUpdateIn(BaseModel):
    """Facet 修改入参——``project_key`` 改走独立 rename 端点（D2 复核）。

    未出现的字段不动；显式 ``null`` 清空可空 facet。
    """

    display_name: Optional[str] = None
    customer: Optional[str] = None
    jira_project_key: Optional[str] = None

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        name = value.strip()
        if not name:
            raise ValueError("display_name must not be blank")
        return name

    @field_validator("jira_project_key")
    @classmethod
    def validate_jira_project_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        key = value.strip()
        if not key:
            return None
        if len(key) > 32:
            raise ValueError("jira_project_key must be at most 32 chars")
        if any(ch.isspace() for ch in key):
            raise ValueError("jira_project_key must not contain whitespace")
        return key


class InventoryModelOut(ORMBaseModel):
    """一种 ``device.model`` 的 fleet 聚合。

    ``mapped_project_keys`` 只含人工 USER 项目（型号的活跃成员行）。
    SEED 回填标签不出现在此字段。
    """

    model: Optional[str] = None
    device_count: int
    platforms: List[str]
    mapped_project_keys: List[str] = []
    unassigned_device_count: int = 0


class InventorySummaryOut(ORMBaseModel):
    total_devices: int
    user_mapped_devices: int
    distinct_models: int
    unmapped_models: List[Optional[str]]
    # ADR-0029 P0：project_id IS NULL 口径（与 GET /devices?unassigned=true
    # 一致，区别于 inventory 的「非 USER 项目」宽口径）
    unassigned_devices: int = 0


class ProjectModelCoverageOut(ORMBaseModel):
    model: Optional[str] = None
    device_count: int
    platforms: List[str]


class ProjectMapIn(BaseModel):
    models: List[str] = Field(min_length=1)
    reassign_conflicts: bool = False


class ProjectMapConflictOut(BaseModel):
    device_id: int
    serial: str
    model: Optional[str] = None
    from_project_key: str


class ProjectMapPreviewOut(BaseModel):
    target_project_key: str
    models: List[str]
    will_assign: int
    already_in_target: int
    conflicts: List[ProjectMapConflictOut] = []
    unknown_models: List[str] = []


class ProjectRenameIn(BaseModel):
    """项目重命名入参（D2 复核：key 是用户指定标识，允许 admin 改名）。

    与 ProjectCreateIn 同格式校验；SEED 保留名在路由层拦截。
    """

    new_key: str = Field(min_length=1, max_length=64)

    @field_validator("new_key")
    @classmethod
    def normalize_new_key(cls, value: str) -> str:
        key = value.strip()
        if not _PROJECT_KEY_RE.match(key):
            raise ValueError("new_key must match [A-Za-z0-9][A-Za-z0-9-]{0,62}")
        return key
