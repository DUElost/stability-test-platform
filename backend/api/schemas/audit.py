from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import Field

from backend.api.schemas.base import ORMBaseModel


class AuditLogOut(ORMBaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[int] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    ip_address: Optional[str] = None
    timestamp: datetime
