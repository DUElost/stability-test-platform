"""计划参数分层投影的响应模型（#3653 U1）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import Field

from backend.api.schemas.base import ORMBaseModel

LayerName = Literal["L1", "L2", "L3", "L4"]
ParamSource = Literal[
    "schema_default",
    "script_default",
    "step_override",
    "dispatch_injection",
]
ParamState = Literal[
    "explicit",
    "unset_definite",
    "env_fallback",
    "pending_dispatch",
    "actual",
]


class ParameterItem(ORMBaseModel):
    path: list[Any]
    label: str
    meaning: str
    unit: Optional[str] = None
    cautions: Optional[str] = None
    source: Optional[ParamSource] = None
    state: ParamState
    value: Any = None
    sensitive: bool = False
    is_set: bool = False
    fallback_chain: Optional[str] = None
    authority: Optional[str] = None
    base_value: Any = None
    decision_factors: list[str] = Field(default_factory=list)
    diagnostic: Optional[str] = None
    ui_editable: Optional[bool] = None
    write_boundary: Optional[str] = None


class ProjectionStep(ORMBaseModel):
    step_key: Optional[str] = None
    script_name: Optional[str] = None
    script_version: Optional[str] = None
    stage: Optional[str] = None
    sort_order: int = 0
    enabled: bool = True
    executes: bool = True
    metadata_missing: bool = False
    missing_reason: Optional[str] = None
    params: list[ParameterItem] = Field(default_factory=list)
    settings: list[ParameterItem] = Field(default_factory=list)


class ProjectionContext(ORMBaseModel):
    plan_id: Optional[int] = None
    plan_run_id: Optional[int] = None
    job_id: Optional[int] = None
    device_id: Optional[int] = None
    host_id: Optional[str] = None
    read_at: datetime
    authority: str


class DispatchDecision(ORMBaseModel):
    kind: str
    state: ParamState
    factors: list[str] = Field(default_factory=list)
    job_id: Optional[int] = None
    device_id: Optional[int] = None
    host_id: Optional[str] = None


class SafeDebugStep(ORMBaseModel):
    step_key: Optional[str] = None
    script_name: Optional[str] = None
    script_version: Optional[str] = None
    stage: Optional[str] = None
    enabled: Optional[bool] = None
    params: dict = Field(default_factory=dict)
    param_schema: dict = Field(default_factory=dict)
    default_params: dict = Field(default_factory=dict)
    step_params: Optional[dict] = None


class SafeDebug(ORMBaseModel):
    steps: list[SafeDebugStep] = Field(default_factory=list)
    plan_settings: dict = Field(default_factory=dict)
    watcher_policy: dict = Field(default_factory=dict)


class WatcherProjection(ORMBaseModel):
    note: str
    items: list[ParameterItem] = Field(default_factory=list)
    effective_policy: Optional[dict] = None
    frozen_host_admin: Optional[dict] = None


class ParameterProjection(ORMBaseModel):
    layer: LayerName
    context: ProjectionContext
    steps: list[ProjectionStep] = Field(default_factory=list)
    plan_settings: list[ParameterItem] = Field(default_factory=list)
    watcher_policy: WatcherProjection
    dispatch_decisions: list[DispatchDecision] = Field(default_factory=list)
    safe_debug: SafeDebug


class ScriptParameterProjection(ORMBaseModel):
    script_name: str
    script_version: str
    params: list[ParameterItem] = Field(default_factory=list)
    safe_debug: SafeDebug
