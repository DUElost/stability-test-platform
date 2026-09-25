"""PlanRun.run_context 的域模块：JSONB 分段写入 helper（SAQ 链与 extract 共用）
+ ADR-0043「abort 在窗」主体判据（消费侧共用，纯函数）。

判据住在**本模块而不是 ``plan_run_abort``**（#3299）：``plan_dispatcher_sync`` 只消费
判据、不消费 abort 流程，此前经 ``plan_run_abort`` 转手 import 是五模块环
（aggregation ↔ post_completion ↔ chain_trigger ↔ dispatcher_sync ↔ abort）
闭合的必要边之一；下沉后 ``dispatcher → plan_run_context`` 是纯向下依赖，
abort 得以指向 ``plan_run_finalization``（终态编排者）而不复形成环。
"""

from __future__ import annotations

import json
import logging
from typing import Iterable

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


# ── ADR-0043「abort 在窗」判据（自 plan_run_abort 迁入，#3299 断环）──────────
# 写入侧仍在 plan_run_abort（run 级 / host 级分支）；此处只放**读**判据——
# 与 device_lease_reconciler 的取时钟规则同源（那里给时间戳取最早者，这里只
# 回答「在不在窗内」）。两侧同 PR 接线、不得单边漂移的纪律不变（#2154）。


def run_abort_pending(run_context: object) -> bool:
    """**run 主体**是否处于 abort 在窗（ADR-0043 主体语义，消费侧共用）。

    run 级 abort 才写 run 级时钟（``abort_requested.at``）；host 级 abort 只维护名单
    （``requested_job_ids``）与 ``abort_requested_hosts[host].at``。此前消费侧一律按
    「``run_context`` 里有 ``abort_requested`` 键」判定——于是**一台主机的 host 级
    abort 会让整个 run（含从未被请求的旁主机）看起来都在 abort 中**：热更新门禁对旁
    主机永久 409，reaper 又按新语义永远不会回收那些 job（#2270）。
    """
    ctx = run_context if isinstance(run_context, dict) else {}
    abort = ctx.get("abort_requested")
    return bool(isinstance(abort, dict) and abort.get("at"))


def abort_pending_job_ids(
    run_context: object,
    jobs: Iterable[tuple[int, str | None]],
) -> set[int]:
    """这些 job 里哪些被**在窗**的 abort 请求覆盖（主体感知，供消费侧共用）。

    - run 主体时钟存在 → 覆盖名单内（名单缺失/为空按历史兼容 = 全部）的 job；
    - host 主体时钟存在 → 覆盖该 host 的 job；
    - 两个时钟都没有（键在但无时钟）→ **不覆盖任何 job**：这正是旧判据造成「旁主机
      永久待中止」的形态。

    与 ``device_lease_reconciler`` 的取时钟规则同源（那里要给时间戳取最早者，这里只
    回答「在不在窗内」）。
    """
    ctx = run_context if isinstance(run_context, dict) else {}
    abort = ctx.get("abort_requested")
    abort = abort if isinstance(abort, dict) else {}
    run_clock = bool(abort.get("at"))
    requested_raw = abort.get("requested_job_ids")
    requested: set[int] = set()
    if isinstance(requested_raw, list):
        for value in requested_raw:
            try:
                requested.add(int(value))
            except (TypeError, ValueError):
                continue
    hosts = ctx.get("abort_requested_hosts")
    hosts = hosts if isinstance(hosts, dict) else {}

    pending: set[int] = set()
    for job_id, host_id in jobs:
        if run_clock and (not requested or job_id in requested):
            pending.add(job_id)
            continue
        clock = hosts.get(host_id) if host_id is not None else None
        if clock is None and host_id is not None:
            clock = hosts.get(str(host_id))
        if isinstance(clock, dict) and clock.get("at"):
            pending.add(job_id)
    return pending


__all__ = [
    "write_run_context_section",
    "run_abort_pending",
    "abort_pending_job_ids",
]
