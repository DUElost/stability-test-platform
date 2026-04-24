from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel, ConfigDict, _HAS_CONFIG_DICT


class TaskScheduleCreate(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(populate_by_name=True)
    else:
        class Config:
            allow_population_by_field_name = True

    name: str
    cron_expression: str = Field(alias="cron_expr")
    task_template_id: Optional[int] = None
    tool_id: Optional[int] = None
    task_type: Optional[str] = "WORKFLOW"
    params: Dict[str, Any] = Field(default_factory=dict, alias="task_params")
    target_device_id: Optional[int] = None
    workflow_definition_id: Optional[int] = None
    device_ids: List[int] = Field(default_factory=list)
    enabled: bool = True


class TaskScheduleUpdate(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(populate_by_name=True)
    else:
        class Config:
            allow_population_by_field_name = True

    name: Optional[str] = None
    cron_expression: Optional[str] = Field(default=None, alias="cron_expr")
    task_template_id: Optional[int] = None
    tool_id: Optional[int] = None
    task_type: Optional[str] = None
    params: Optional[Dict[str, Any]] = Field(default=None, alias="task_params")
    target_device_id: Optional[int] = None
    workflow_definition_id: Optional[int] = None
    device_ids: Optional[List[int]] = None
    enabled: Optional[bool] = None


class TaskScheduleOut(ORMBaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    else:
        class Config:
            orm_mode = True
            allow_population_by_field_name = True

    id: int
    name: str
    cron_expression: str = Field(alias="cron_expr")
    task_template_id: Optional[int] = None
    tool_id: Optional[int] = None
    task_type: str
    params: Dict[str, Any] = Field(default_factory=dict, alias="task_params")
    target_device_id: Optional[int] = None
    workflow_definition_id: Optional[int] = None
    device_ids: Optional[List[int]] = None
    enabled: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: datetime
