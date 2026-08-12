from typing import Any, List, Optional
from uuid import UUID

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


class OrphanLogSignalOut(ORMBaseModel):
    """``job_log_signal`` row with ``job_id IS NULL`` (#213 D3 / #212 P1-7)."""

    id: int
    host_id: str
    device_serial: str
    seq_no: int
    category: str
    source: str
    path_on_device: str
    artifact_uri: Optional[str] = None
    detected_at: Any = None
    received_at: Any = None
    device_log_event_id: Optional[UUID] = None
    extra: Optional[dict] = None


class OrphanLogSignalListOut(BaseModel):
    items: List[OrphanLogSignalOut]
    total: int
    skip: int
    limit: int
    excluding_call_sites: List[str] = Field(
        default_factory=list,
        description="PlanRun-scoped aggregations that intentionally omit orphans",
    )
