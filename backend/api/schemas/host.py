from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from backend.api.schemas.base import ORMBaseModel


class HostCreate(BaseModel):
    name: str
    ip: str
    ssh_port: int = 22
    ssh_user: Optional[str] = None
    ssh_auth_type: str = "password"
    ssh_key_path: Optional[str] = None


class HostOut(ORMBaseModel):
    id: str
    name: Optional[str] = None
    ip: Optional[str] = None
    ssh_port: Optional[int] = 22
    ssh_user: Optional[str] = None
    ssh_auth_type: Optional[str] = None
    status: str
    last_heartbeat: Optional[datetime] = None
    extra: Dict[str, Any] = {}
    mount_status: Dict[str, Any] = {}

    @field_validator('extra', 'mount_status', mode='before')
    @classmethod
    def _coerce_none_to_dict(cls, v):
        return v or {}


class HostLiteOut(ORMBaseModel):
    id: str
    name: Optional[str] = None
    ip: Optional[str] = None
    status: str


class HeartbeatIn(BaseModel):
    host_id: str
    status: Literal["ONLINE", "OFFLINE", "DEGRADED"]
    tool_catalog_version: str = ""
    script_catalog_version: str = ""
    mount_status: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
    host: Optional[Dict[str, Any]] = None
    devices: List[Dict[str, Any]] = Field(default_factory=list)
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1

    @field_validator('host_id', mode='before')
    @classmethod
    def coerce_str(cls, v):
        return str(v)
