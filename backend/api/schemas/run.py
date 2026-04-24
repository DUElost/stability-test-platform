from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel
from backend.api.schemas.task import TaskOut
from backend.api.schemas.host import HostLiteOut
from backend.api.schemas.device import DeviceLiteOut


class LogArtifactOut(ORMBaseModel):
    id: int
    run_id: int
    storage_uri: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    created_at: datetime


class RunOut(ORMBaseModel):
    id: int
    task_id: int
    host_id: int
    device_id: int
    status: str
    group_id: Optional[str] = None
    progress: int = 0
    progress_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    log_summary: Optional[str] = None
    artifacts: List["LogArtifactOut"] = Field(default_factory=list)
    risk_summary: Optional[Dict[str, Any]] = None


class RunUpdate(BaseModel):
    status: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    exit_code: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    log_summary: Optional[str] = None
    log_lines: Optional[List[str]] = None
    progress: Optional[int] = None
    progress_message: Optional[str] = None


class LogArtifactIn(BaseModel):
    storage_uri: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None


class RunCompleteIn(BaseModel):
    update: RunUpdate
    artifact: Optional[LogArtifactIn] = None


class RunAgentOut(BaseModel):
    id: int
    task_id: int
    host_id: int
    device_id: int
    device_serial: Optional[str] = None
    task_type: str
    task_params: Dict[str, Any] = Field(default_factory=dict)
    tool_id: Optional[int] = None
    tool_snapshot: Optional[Dict[str, Any]] = None
    pipeline_def: Optional[Dict[str, Any]] = None


class RunStepCreate(BaseModel):
    run_id: int
    phase: str
    step_order: int
    name: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)


class RunStepUpdate(BaseModel):
    status: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    log_line_count: Optional[int] = None


class RunStepOut(ORMBaseModel):
    id: int
    run_id: int
    phase: str
    step_order: int
    name: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    log_line_count: int = 0
    created_at: datetime


class RiskAlertOut(BaseModel):
    code: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    message: str
    metric: Optional[str] = None
    value: Optional[int] = None
    threshold: Optional[int] = None


class RunReportOut(ORMBaseModel):
    generated_at: datetime
    run: RunOut
    task: TaskOut
    host: Optional[HostLiteOut] = None
    device: Optional[DeviceLiteOut] = None
    summary_metrics: Dict[str, Any] = Field(default_factory=dict)
    risk_summary: Optional[Dict[str, Any]] = None
    alerts: List[RiskAlertOut] = Field(default_factory=list)


class JiraDraftOut(BaseModel):
    run_id: int
    task_id: int
    project_key: str
    issue_type: str = "Bug"
    priority: Literal["Critical", "Major", "Minor"]
    component: Optional[str] = None
    fix_version: Optional[str] = None
    assignee: Optional[str] = None
    summary: str
    description: str
    labels: List[str] = Field(default_factory=list)
    environment: Dict[str, Any] = Field(default_factory=dict)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
