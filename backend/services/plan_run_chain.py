"""PlanRun 执行链上下文（#1520 垂直切片：GET /plan-runs/{id}/chain）。

ADR-0020 §6：沿 root 收集已触发 PlanRun，再沿 ``next_plan_id`` 展开未触发
占位节点；``seen_plans`` + ``MAX_CHAIN_DEPTH`` 防环（#753）。

路由退化为 ``_require_plan_run`` + ``ok(build_plan_run_chain(...))``。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import ChainNodeOut, PlanChainOut
from backend.models.enums import PlanRunStatus
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.plan_run_read_common import duration_seconds, iso

# Mirror write-side ``backend.api.routes.plans.MAX_CHAIN_DEPTH``（#753）。
# 不从 routes 反向 import（#1519）。
MAX_CHAIN_DEPTH = 20


def chain_node_from_run(
    pr: PlanRun,
    plan_name: Optional[str],
    is_current: bool,
) -> ChainNodeOut:
    summary = pr.result_summary or {}
    pass_rate = summary.get("pass_rate") if isinstance(summary, dict) else None
    return ChainNodeOut(
        plan_id=pr.plan_id,
        plan_name=plan_name,
        plan_run_id=pr.id,
        status=pr.status,
        chain_index=pr.chain_index or 0,
        started_at=iso(pr.started_at),
        ended_at=iso(pr.ended_at),
        duration_seconds=duration_seconds(pr.started_at, pr.ended_at),
        failure_threshold=pr.failure_threshold,
        pass_rate=pass_rate,
        is_current=is_current,
    )


# 既有测试 / 调用方可能以私有名导入。
_chain_node_from_run = chain_node_from_run


def build_plan_run_chain(db: Session, pr: PlanRun) -> PlanChainOut:
    """ADR-0020 §6: PlanRun chain 上下文 — 沿 parent_plan_run_id 回溯到 root,
    再沿 next_plan_id 展开到链尾（2026-09-01 起展示完整未触发链）。

    返回的 nodes 列表按 chain_index 升序,包含:
      - 0..N 个 parent PlanRun (已触发)
      - 1 个 current PlanRun(is_current=True)
      - 0..N 个未触发的 next Plan 节点(plan_run_id=None, status='pending')——
        沿 next_plan_id 每个未触发链节一个占位节点;仅链首节点承载
        is_blocked/block_reason,后续节点固定 block_reason="等待前序 Plan 触发"
    """
    # 1) 沿 chain 收集所有已存在的 PlanRun
    root_id = pr.root_plan_run_id or pr.id
    chain_runs = db.execute(
        select(PlanRun)
        .where((PlanRun.id == root_id) | (PlanRun.root_plan_run_id == root_id))
        .order_by(PlanRun.chain_index.asc(), PlanRun.id.asc())
    ).scalars().all()

    # 2) 批量取 plan name
    plan_ids = list({r.plan_id for r in chain_runs})
    plan_names: dict[int, str] = {}
    if plan_ids:
        rows = db.execute(
            select(Plan.id, Plan.name).where(Plan.id.in_(plan_ids))
        ).all()
        plan_names = {r.id: r.name for r in rows}

    nodes = [
        chain_node_from_run(r, plan_names.get(r.plan_id), is_current=(r.id == pr.id))
        for r in chain_runs
    ]

    # 3) next Plan 完整链:取 chain_runs 末尾 PlanRun 对应 Plan.next_plan_id,
    #    沿 next_plan_id 展开到链尾——未触发节点全部 pending 占位（2026-09-01
    #    「执行链未显示完整」反馈:原实现只显示 1 跳,后续链节丢失）。
    if chain_runs:
        tail = chain_runs[-1]
        tail_plan = db.get(Plan, tail.plan_id)
        if tail_plan and tail_plan.next_plan_id and not tail.next_plan_triggered:
            first_next = db.get(Plan, tail_plan.next_plan_id)
            if first_next is not None:
                # 推断 block 原因（仅第一个 next——后续 pending 等前序触发）
                blocked = False
                reason = None
                summary = tail.result_summary or {}
                chain_fail = (
                    summary.get("chain_dispatch_failed")
                    if isinstance(summary, dict)
                    else None
                )
                if isinstance(chain_fail, dict) and chain_fail.get("error"):
                    blocked = True
                    reason = f"下游 Plan 派发失败: {chain_fail['error']}"
                elif tail.status == PlanRunStatus.RUNNING.value:
                    blocked = True
                    reason = "parent PlanRun 仍在运行,需等待终态"
                elif tail.status not in (
                    PlanRunStatus.SUCCESS.value,
                    PlanRunStatus.PARTIAL_SUCCESS.value,
                ):
                    blocked = True
                    pr_summary_failed = (
                        summary.get("failed", 0) if isinstance(summary, dict) else 0
                    )
                    pr_summary_total = (
                        summary.get("total", 0) if isinstance(summary, dict) else 0
                    )
                    if pr_summary_total:
                        rate = pr_summary_failed / pr_summary_total
                        reason = (
                            f"failure_rate {rate:.1%} > threshold "
                            f"{tail.failure_threshold:.1%}; chain 终止"
                        )
                    else:
                        reason = f"parent status={tail.status}; chain 不触发"
                elif tail.status in (
                    PlanRunStatus.SUCCESS.value,
                    PlanRunStatus.PARTIAL_SUCCESS.value,
                ):
                    blocked = True
                    reason = "等待下游 Plan 自动派发"

                # #753: read path must mirror write-side DAG guards — a 2+ cycle
                # (API-bypassing SQL) previously hung this GET forever.
                cursor = first_next
                idx = (tail.chain_index or 0) + 1
                seen_plans: set[int] = {r.plan_id for r in chain_runs}
                depth = 0
                while cursor is not None:
                    if cursor.id in seen_plans:
                        break
                    depth += 1
                    if depth > MAX_CHAIN_DEPTH:
                        break
                    seen_plans.add(cursor.id)
                    nodes.append(ChainNodeOut(
                        plan_id=cursor.id,
                        plan_name=cursor.name,
                        plan_run_id=None,
                        status="pending",
                        chain_index=idx,
                        failure_threshold=cursor.failure_threshold,
                        is_blocked=blocked if cursor.id == first_next.id else False,
                        block_reason=(
                            reason if cursor.id == first_next.id else "等待前序 Plan 触发"
                        ),
                    ))
                    if cursor.next_plan_id is None:
                        break
                    cursor = db.get(Plan, cursor.next_plan_id)
                    idx += 1

    return PlanChainOut(
        plan_run_id=pr.id,
        root_plan_run_id=root_id,
        nodes=nodes,
    )
