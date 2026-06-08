from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.api.schemas.base import ORMBaseModel


class TaskScheduleCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    cron_expression: str = Field(alias="cron_expr")
    plan_id: int
    device_ids: List[int] = Field(default_factory=list)
    enabled: bool = True


class TaskScheduleUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    cron_expression: Optional[str] = Field(default=None, alias="cron_expr")
    plan_id: Optional[int] = None
    device_ids: Optional[List[int]] = None
    enabled: Optional[bool] = None


class TaskScheduleOut(ORMBaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    name: str
    cron_expression: str = Field(alias="cron_expr")
    plan_id: int
    device_ids: Optional[List[int]] = None
    enabled: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: datetime
