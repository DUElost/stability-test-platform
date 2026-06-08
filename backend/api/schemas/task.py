from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel


class TaskCreate(BaseModel):
    name: str
    type: str
    template_id: Optional[int] = None
    tool_id: Optional[int] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    tool_snapshot: Optional[Dict[str, Any]] = None
    target_device_id: Optional[int] = None
    device_serial: Optional[str] = None
    priority: int = 0
    is_distributed: bool = False
    device_ids: Optional[List[int]] = None
    pipeline_def: Optional[Dict[str, Any]] = None


class TaskOut(ORMBaseModel):
    id: int
    name: str
    type: str
    template_id: Optional[int] = None
    tool_id: Optional[int] = None
    params: Dict[str, Any] = {}
    tool_snapshot: Optional[Dict[str, Any]] = None
    target_device_id: Optional[int] = None
    status: str
    priority: int
    group_id: Optional[str] = None
    is_distributed: bool = False
    runs_count: Optional[int] = None
    pipeline_def: Optional[Dict[str, Any]] = None
    created_at: datetime


class TaskDispatch(BaseModel):
    host_id: int
    device_id: int
