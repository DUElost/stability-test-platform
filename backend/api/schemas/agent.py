from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel


class AgentLogQuery(BaseModel):
    host_id: int
    log_path: str = "/tmp/agent.log"
    lines: int = 100


class AgentLogOut(BaseModel):
    host_id: int
    log_path: str
    content: str
    lines_read: int
    error: Optional[str] = None


class DeploymentCreate(BaseModel):
    install_path: str = "/opt/stability-test-agent"


class DeploymentOut(ORMBaseModel):
    id: int
    host_id: int
    status: str
    install_path: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    logs: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


class DeploymentStatusOut(ORMBaseModel):
    deployment_id: int
    host_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None
