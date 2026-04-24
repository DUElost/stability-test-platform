from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel


class WorkflowStepCreate(BaseModel):
    name: str
    tool_id: Optional[int] = None
    task_type: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    target_device_id: Optional[int] = None


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    steps: List[WorkflowStepCreate]


class WorkflowStepOut(ORMBaseModel):
    id: int
    workflow_id: int
    order: int
    name: str
    tool_id: Optional[int] = None
    task_type: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    target_device_id: Optional[int] = None
    status: str
    task_run_id: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class WorkflowOut(ORMBaseModel):
    id: int
    name: str
    description: Optional[str] = None
    status: str
    is_template: bool = False
    created_by: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    steps: List[WorkflowStepOut] = Field(default_factory=list)
