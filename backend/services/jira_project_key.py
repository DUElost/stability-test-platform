"""JIRA 项目键解析（G17）——全系统唯一解析入口，快照口径。

消费方：dedup 提单（source=plan_run）与 runs 草稿端点。统一从
``plan_run.project_id`` 快照解析——派发时冻结的归属为准，Plan 事后改归属
不影响历史 Run 的 JIRA 目标（ADR-0029 D5，与 results.py 同口径）。
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def resolve_jira_project_key(db: Session, plan_run_id: Optional[int]) -> Optional[str]:
    """PlanRun.project_id 快照 → test_project.jira_project_key 解析链。

    任一环缺失/未配置返回 None——提单是旁路功能，映射缺失只记日志不阻断
    （厂商工具自己的默认映射仍生效）；硬门禁属 G18 自动草稿策略的范畴。
    解析异常同样吞掉记 ERROR，保证主流程不受影响。
    """
    if plan_run_id is None:
        return None
    try:
        from backend.models.plan_run import PlanRun
        from backend.models.project import TestProject

        run = db.get(PlanRun, plan_run_id)
        project_id = getattr(run, "project_id", None) if run else None
        project = db.get(TestProject, project_id) if project_id else None
        key = (getattr(project, "jira_project_key", "") or "").strip() if project else ""
        return key or None
    except Exception:
        logger.exception("jira_project_key_resolve_failed plan_run=%s", plan_run_id)
        return None
