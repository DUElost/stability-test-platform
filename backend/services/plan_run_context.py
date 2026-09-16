"""PlanRun.run_context JSONB 分段写入 helper（SAQ 链与 extract 共用）。"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def write_run_context_section(
    db: Session,
    plan_run_id: int,
    section: str,
    value: dict,
) -> bool:
    """把 ``value`` 写入 ``PlanRun.run_context[section]`` 并 commit。

    #2285：改用**库端** ``jsonb_set`` 单段写入（先例：``plan_run_abort._patch_run_context``
    / ``dedup_scan.record_scan_archive_state``）。此前是「读整段 → 改一个键 → 整段写回」，
    而同一行会被多个独立会话并发更新（abort 的 host 时钟、``dispatch_state``、各 SAQ
    任务、admission_pump 的读改写）——整段写回会把并发写者刚落下的键抹掉，表现为该
    阶段回落为「无记录 / unknown」。单段路径不涉及 ``jsonb_set``「只创建末段」的限制
    （父键缺失时它不会递归创建），故无需前置建键。

    兼容旧数据：``run_context`` 为 SQL NULL / JSON null / 非对象时按空对象重建。
    返回是否更新到行；plan_run 不存在时返回 False。
    """
    result = db.execute(
        text(
            "UPDATE plan_run SET run_context = jsonb_set("
            # SQLAlchemy JSON 列的 None 落库为 JSON null（非 SQL NULL），
            # COALESCE 不会替换它 → 需显式 NULLIF，否则 jsonb_set 报
            # "cannot set path in scalar"（同 plan_run_abort 的实测注记）。
            "  COALESCE(NULLIF(run_context, 'null'::jsonb), '{}'::jsonb), "
            "  CAST(:path AS text[]), CAST(:value AS jsonb), true"
            ") WHERE id = :run_id"
        ),
        {
            "run_id": plan_run_id,
            "path": [str(section)],
            "value": json.dumps(value, ensure_ascii=False, default=str),
        },
    )
    db.commit()
    return bool(result.rowcount)


__all__ = ["write_run_context_section"]
