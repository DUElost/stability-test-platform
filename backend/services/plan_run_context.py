"""PlanRun.run_context JSONB 分段写入 helper（SAQ 链与 extract 共用）。"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from backend.models.plan_run import PlanRun

logger = logging.getLogger(__name__)


def write_run_context_section(
    db: Session,
    plan_run_id: int,
    section: str,
    value: dict,
) -> bool:
    """把 ``value`` 写入 ``PlanRun.run_context[section]`` 并 commit。

    兼容旧数据：run_context 为 None / 非 dict 时按空 dict 重建。
    返回是否更新到行；plan_run 不存在时返回 False。
    """
    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        return False
    rc = dict(pr.run_context) if isinstance(pr.run_context, dict) else {}
    rc[section] = value
    pr.run_context = rc
    db.commit()
    return True


__all__ = ["write_run_context_section"]
