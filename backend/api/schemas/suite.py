"""ADR-0030 P1a — TestSuite / TestCase 对外 schema。

套件对外键是 ``name``（PlanCreate/PlanUpdate 的 ``suite_name``；数字 id 仍
出现在 URL ``/test-suites/{id}``），与 ADR-0029 的 project_key 口径不同——
套件是配置实体、可重命名，稳定引用用 id，可读引用用 name。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel


class TestCaseOut(ORMBaseModel):
    id: int
    name: str
    ordinal: int
    times: int
    enabled: bool
    exec_descs: List[Dict[str, Any]] = Field(default_factory=list)


class TestCaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    ordinal: int = 0
    times: int = Field(default=1, ge=1)
    enabled: bool = True
    exec_descs: List[Dict[str, Any]] = Field(default_factory=list)


class TestSuiteOut(ORMBaseModel):
    """列表行。"""

    id: int
    name: str
    display_name: Optional[str] = None
    project_key: Optional[str] = None
    export_dir: Optional[str] = None
    apk_binding: Optional[List[str]] = None
    case_count: int = 0
    enabled_case_count: int = 0
    exported_sha256: Optional[str] = None
    is_active: bool = True
    # 库内容是否已漂离最近一次导出（= 门禁第 3 步会拦的状态，列表即可见）
    export_stale: bool = False
    created_at: datetime
    updated_at: datetime


class TestSuiteDetailOut(TestSuiteOut):
    root_config: Dict[str, Any] = Field(default_factory=dict)
    global_params: Optional[Dict[str, Any]] = None
    source_sha256: Optional[str] = None
    exported_content_sha256: Optional[str] = None
    content_sha256: Optional[str] = None   # 当前库内容指纹（与上一列比即知是否 stale）


class TestSuiteCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=256)
    project_key: Optional[str] = None
    export_dir: Optional[str] = Field(default=None, max_length=128)
    apk_binding: Optional[List[str]] = None
    root_config: Dict[str, Any] = Field(default_factory=dict)
    global_params: Optional[Dict[str, Any]] = None


class TestSuiteUpdateIn(BaseModel):
    """PUT 元数据；未提供的字段不改（None 与「不提供」不可区分的字段用哨兵语义说明）。"""

    display_name: Optional[str] = Field(default=None, max_length=256)
    project_key: Optional[str] = None
    export_dir: Optional[str] = Field(default=None, max_length=128)
    apk_binding: Optional[List[str]] = None
    root_config: Optional[Dict[str, Any]] = None
    global_params: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class IssueOut(BaseModel):
    severity: str
    code: str
    message: str
    testpoint: Optional[str] = None


class ValidateOut(BaseModel):
    valid: bool
    issues: List[IssueOut] = Field(default_factory=list)


class ExportResultOut(BaseModel):
    export_dir: str
    runtask_path: str
    global_path: Optional[str] = None
    exported_sha256: str
    exported_content_sha256: str
