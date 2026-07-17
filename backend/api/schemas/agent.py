from typing import Optional

from pydantic import BaseModel


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
