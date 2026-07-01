from datetime import datetime
from typing import List, Optional

from backend.api.schemas.base import ORMBaseModel


class JiraRunOut(ORMBaseModel):
    """批量提单 run 的持久化记录（历史记录列表/详情）。"""

    id: int
    console_run_id: str
    vendor: str
    stage: str
    dry_run: bool
    reporter: Optional[str] = None
    input_source: str
    plan_run_id: Optional[int] = None
    artifact_id: Optional[int] = None
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    issue_keys: List[str] = []
    error: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_at: datetime