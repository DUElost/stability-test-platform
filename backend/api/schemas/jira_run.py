from datetime import datetime
from typing import Any, List, Optional

from backend.api.schemas.base import ORMBaseModel


class JiraOccurrenceLinkOut(ORMBaseModel):
    """B8 G8：有证据支持的 Jira issue key ↔ 问题/发生关联。"""

    id: int
    jira_run_id: int
    plan_run_id: int
    platform: str
    delivery_version_id: Optional[int] = None
    delivery_version_no: Optional[int] = None
    signature_id: int
    occurrence_id: int
    issue_key: str
    evidence_source: str
    created_at: datetime


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
    platform: Optional[str] = None
    delivery_version_id: Optional[int] = None
    delivery_version_no: Optional[int] = None
    compat_rerun_id: Optional[int] = None
    workspace_dir: Optional[str] = None
    attribution_status: Optional[str] = None
    attribution_gaps: List[dict[str, Any]] = []
    occurrence_links: List[JiraOccurrenceLinkOut] = []
    jira_project_key: Optional[str] = None
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    issue_keys: List[str] = []
    error: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_at: datetime