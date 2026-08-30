"""ADR-0030 P2 — test_case_result API schema。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel


class TestCaseResultOut(ORMBaseModel):
    id: int
    plan_run_id: int
    job_id: int
    suite_id: Optional[int] = None
    case_id: Optional[int] = None
    case_name: str
    status: str
    detail: Optional[str] = None
    artifact_uri: Optional[str] = None
    run_dir: Optional[str] = None
    created_at: datetime
    device_id: Optional[int] = None
    host_id: Optional[str] = None


class TestCaseResultSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    error: int = 0


class TestCaseResultsPayload(BaseModel):
    items: List[TestCaseResultOut] = Field(default_factory=list)
    total: int = 0
    summary: TestCaseResultSummary = Field(default_factory=TestCaseResultSummary)
