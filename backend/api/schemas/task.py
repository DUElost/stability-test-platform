from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel, ConfigDict, _HAS_CONFIG_DICT


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


class TaskTemplateOut(BaseModel):
    type: str
    name: str
    description: str
    default_params: Dict[str, Any] = Field(default_factory=dict)
    script_paths: Dict[str, str] = Field(default_factory=dict)


class TaskDispatch(BaseModel):
    host_id: int
    device_id: int


# TaskTemplate DB-backed CRUD

class TaskTemplateDBCreate(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(populate_by_name=True)
    else:
        class Config:
            allow_population_by_field_name = True

    name: str
    type: str = Field(alias="task_type")
    description: Optional[str] = None
    default_params: Dict[str, Any] = Field(default_factory=dict, alias="params")
    enabled: bool = True


class TaskTemplateDBUpdate(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(populate_by_name=True)
    else:
        class Config:
            allow_population_by_field_name = True

    name: Optional[str] = None
    type: Optional[str] = Field(default=None, alias="task_type")
    description: Optional[str] = None
    default_params: Optional[Dict[str, Any]] = Field(default=None, alias="params")
    enabled: Optional[bool] = None


class TaskTemplateDBOut(ORMBaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    else:
        class Config:
            orm_mode = True
            allow_population_by_field_name = True

    id: int
    name: str
    type: str = Field(alias="task_type")
    description: Optional[str] = None
    default_params: Dict[str, Any] = Field(default_factory=dict, alias="params")
    enabled: bool
    created_at: datetime
