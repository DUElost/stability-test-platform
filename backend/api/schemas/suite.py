"""ADR-0030 P1a — TestSuite / TestCase 对外 schema。

套件对外键是 ``name``（PlanCreate/PlanUpdate 的 ``suite_name``；数字 id 仍
出现在 URL ``/test-suites/{id}``），与 ADR-0029 的 project_key 口径不同——
套件是配置实体、可重命名，稳定引用用 id，可读引用用 name。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.api.schemas.base import ORMBaseModel


def normalize_export_dir(value: str) -> str:
    """#968 契约：``export_dir`` 是相对存储根的目录名 / 相对子路径。

    拒绝绝对路径、``..`` 组件、空串与 NUL；其余规范化（折叠 ``.``、去尾斜杠）。
    调用方（schema 写入口、导出端点）把 ``ValueError`` 文案转 422。
    """
    candidate = value.strip() if isinstance(value, str) else ""
    if not candidate:
        raise ValueError("export_dir must be a non-empty string")
    if "\x00" in candidate:
        raise ValueError("export_dir must not contain NUL")
    parts = PurePosixPath(candidate).parts
    if not parts or parts[0] == "/" or ".." in parts:
        raise ValueError("export_dir must be a relative path without '..'")
    return str(PurePosixPath(candidate))


def _validate_exec_descs(descs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """#969 写边界字段级校验：保证入库的 exec_descs 可被读路径消费。

    ``times`` 缺省/空串按 1；显式值须可转 int 且 ≥1（0 会被读路径静默改成 1，
    负值会原样渲染进 XML，均拒绝）。``args`` 须为对象。其余键原样保留。
    """
    for index, raw in enumerate(descs):
        if not isinstance(raw, dict):
            raise ValueError(f"exec_descs[{index}] must be a JSON object")
        raw_times = raw.get("times")
        if raw_times in (None, ""):
            times_value = 1
        else:
            try:
                times_value = int(raw_times)
            except (TypeError, ValueError):
                raise ValueError(f"exec_descs[{index}].times must be an integer") from None
        if times_value < 1:
            raise ValueError(f"exec_descs[{index}].times must be >= 1")
        args = raw.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"exec_descs[{index}].args must be a JSON object")
    return descs


class TestCaseOut(ORMBaseModel):
    id: int
    name: str
    ordinal: int
    times: int
    enabled: bool
    exec_descs: List[Dict[str, Any]] = Field(default_factory=list)


class TestCaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    ordinal: Optional[int] = None
    times: int = Field(default=1, ge=1)
    enabled: bool = True
    exec_descs: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("exec_descs")
    @classmethod
    def _check_exec_descs(cls, value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return _validate_exec_descs(value)


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
    exported_global_sha256: Optional[str] = None
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

    @field_validator("export_dir")
    @classmethod
    def _normalize_export_dir(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else normalize_export_dir(value)


class TestSuiteUpdateIn(BaseModel):
    """PUT 元数据；未提供的字段不改（None 与「不提供」不可区分的字段用哨兵语义说明）。

    #939：is_active 列 NOT NULL——显式 ``null`` 在 schema 层拒绝为 422（放行
    会在提交期 500）；可空列（display_name/export_dir）显式 ``null`` 清空语义
    保留。
    """

    display_name: Optional[str] = Field(default=None, max_length=256)
    project_key: Optional[str] = None
    export_dir: Optional[str] = Field(default=None, max_length=128)
    apk_binding: Optional[List[str]] = None
    root_config: Optional[Dict[str, Any]] = None
    global_params: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def reject_explicit_null_on_nonnullable(self) -> "TestSuiteUpdateIn":
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("is_active cannot be null (NOT NULL column)")
        return self

    @field_validator("export_dir")
    @classmethod
    def _normalize_export_dir(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else normalize_export_dir(value)


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
    exported_global_sha256: Optional[str] = None
