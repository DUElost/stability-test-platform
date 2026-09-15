"""ADR-0021 — PlanRun abort service.

Independent of the dispatch gate.  ``abort_plan_run`` is the single entry
point used by both the public ``POST /api/v1/plan-runs/{id}/abort`` API
and the host hot-update flow (``abort_running_jobs=true``).

Termination contract:

- PRECHECK / SYNCING / not-yet-dispatched RUNNING:
  - mark PlanRun status='FAILED' + result_summary={precheck_failed: True,
    reason: 'aborted_by_user'}
  - run_context.precheck.final_result='aborted'
  - no jobs to release
- RUNNING with active jobs:
  - PENDING jobs → status=ABORTED inline
  - RUNNING jobs keep their ACTIVE lease, receive an abort control command,
    terminate the process tree, then report ABORTED through the canonical
    completion endpoint
  - unresponsive agents move to UNKNOWN/quarantine; the device is never
    reallocated while the old process may still be alive
- already terminal: no-op, returns False

Always writes audit_log(action='abort_plan_run').

The function is **non-blocking**: it does not wait for agents to respond.
Frontend re-renders via SocketIO room ``plan_run:{id}``.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from backend.realtime.socketio_server import (
    schedule_agent_control_fanout,
    schedule_emit,
)
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.core.audit import record_audit
from backend.core.job_timeout_config import ABORT_ACK_GRACE_SECONDS
from backend.core.metrics import record_plan_run_abort_lock_seconds
from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.plan_run_aggregation import (
    apply_plan_run_aggregation,
    apply_plan_run_aggregation_from_counters,
    notify_plan_run_terminal,
)
from backend.services.dedup_scan import should_trigger_dedup, enqueue_dedup_terminal_sync
from backend.services.state_machine import PlanRunStateMachine

logger = logging.getLogger(__name__)


_TERMINAL_PLAN_RUN_STATUSES = {
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
}

_TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}

_ACTIVE_JOB_STATUSES = (
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
)


class PlanRunAbortError(Exception):
    """Raised when an abort is rejected for state-machine reasons."""


def _bulk_abort_pending_jobs(
    db: Session,
    pr: PlanRun,
    plan_run_id: int,
    pending_ids: list[int],
    pending_host_by_id: dict[int, Optional[str]],
    *,
    reason: str,
    triggered_by: Optional[str],
    now: datetime,
) -> list[int]:
    """PENDING → ABORTED 批量终态化（#492/#988 语义），返回实际迁移的 job id。

    与逐条 ``transition`` 等价：批量块末尾调用一次计数器聚合（run 级 + 每 host
    级），审计聚合为一条 batch 记录。``WHERE status='PENDING'`` 兼作状态机校验；
    Agent 在预读后 claim→RUNNING 的行不会被更新，由调用方按「运行中」处理。
    """
    result = db.execute(
        update(JobInstance)
        .where(
            JobInstance.id.in_(pending_ids),
            JobInstance.status == JobStatus.PENDING.value,
        )
        .values(
            status=JobStatus.ABORTED.value,
            status_reason=reason,
            execution_state=None,  # 同 transition 的终态清理语义
            ended_at=now,
            updated_at=now,
        )
        .returning(JobInstance.id),
    )
    aborted_ids = [row[0] for row in result.all()]
    n = len(aborted_ids)
    if not n:
        return []

    pr.aborted_job_count = int(pr.aborted_job_count or 0) + n
    pr.terminal_job_count = int(pr.terminal_job_count or 0) + n
    host_counts = Counter(
        pending_host_by_id[jid]
        for jid in aborted_ids
        if pending_host_by_id.get(jid)
    )
    for hid, cnt in host_counts.items():
        db.execute(
            update(PlanRunHost)
            .where(
                PlanRunHost.plan_run_id == plan_run_id,
                PlanRunHost.host_id == hid,
            )
            .values(
                aborted_job_count=PlanRunHost.aborted_job_count + cnt,
                terminal_job_count=PlanRunHost.terminal_job_count + cnt,
            )
        )
    record_audit(
        db,
        action="job_batch_terminalized",
        resource_type="plan_run",
        resource_id=plan_run_id,
        details={
            "count": n,
            "from_status": JobStatus.PENDING.value,
            "to_status": JobStatus.ABORTED.value,
            "plan_run_id": plan_run_id,
            "hosts": dict(host_counts),
        },
        username=triggered_by or "system",
    )
    total = int(pr.total_job_count or 0)
    if total > 0:
        apply_plan_run_aggregation_from_counters(pr)
    return aborted_ids


# #1928：`_record_host_abort_request` 曾写 `run_context['abort_requested_hosts']`
# 却全仓无调用方也无消费者（reaper 只读 run 级 `abort_requested` 的存在性），
# 属「文档宣称 > 实现」的孤儿结构，按最小面移除。
# ADR-0043（Accepted v1.1，#2154）：该键**重新引入**为 host 主体的宽限时钟真源
# ——写入在下方 host 级 abort 分支，消费在
# `device_lease_reconciler._reconcile_aborted_running_jobs`（按主体取时钟、并存
# 时取更早者）。与 #1928 孤儿形态的区别是**有写有读**：写入侧在本文件，消费侧
# 在 reaper，两侧同 PR 接线，不得再出现单边写入。



def _patch_run_context(db: Session, plan_run_id: int, path: list, value) -> None:
    """#793：以库端 jsonb_set 分段更新 run_context。

    先例 dedup_scan.record_scan_archive_state：abort / 归档 / dispatch_state 可能
    同时更新同一行，整段读改写回会把并发写者的键抹掉（本函数此前四处
    ``pr.run_context = run_ctx`` 均属该形态）。``path`` 为 jsonb_set 路径段
    （如 ``["abort_requested", "requested_job_ids"]``）。
    """
    import json

    from sqlalchemy import text

    db.execute(
        text(
            "UPDATE plan_run SET run_context = jsonb_set("
            # SQLAlchemy JSON 列的 None 落库为 JSON null（非 SQL NULL），
            # COALESCE 不会替换它 → 需显式 NULLIF，否则 jsonb_set 报
            # "cannot set path in scalar"。
            "  COALESCE(NULLIF(run_context, 'null'::jsonb), '{}'::jsonb), "
            "  CAST(:path AS text[]), CAST(:value AS jsonb), true"
            ") WHERE id = :run_id"
        ),
        {
            "run_id": plan_run_id,
            "path": [str(seg) for seg in path],
            "value": json.dumps(value, ensure_ascii=False, default=str),
        },
    )


def _ensure_abort_hosts_key(db: Session, plan_run_id: int) -> None:
    """确保 ``abort_requested_hosts`` 已存在且为对象（写 host 级时钟的前置）。

    PG 的 ``jsonb_set`` **只创建 path 的最后一段**：``{abort_requested_hosts,
    <host_id>}`` 在父键缺失时不会递归创建（实测 ``jsonb_set('{}'::jsonb,
    '{x,y}', '"v"'::jsonb, true)`` 返回 ``{}``），故两段路径的写入会静默无效
    （rowcount 仍为 1）。父键用**库端** ``COALESCE`` 取当前值，不覆盖并发写者
    已写入的其它 host 时钟。
    """
    from sqlalchemy import text

    db.execute(
        text(
            "UPDATE plan_run SET run_context = jsonb_set("
            "  COALESCE(NULLIF(run_context, 'null'::jsonb), '{}'::jsonb), "
            "  CAST(:path AS text[]), "
            "  COALESCE("
            "    CASE WHEN jsonb_typeof(run_context -> 'abort_requested_hosts')"
            "              = 'object' "
            "         THEN run_context -> 'abort_requested_hosts' END, "
            "    '{}'::jsonb"
            "  ), true"
            ") WHERE id = :run_id"
        ),
        {"run_id": plan_run_id, "path": ["abort_requested_hosts"]},
    )


def _reload_run_context(db: Session, pr: PlanRun) -> None:
    """#1552：分段写之后同步本 session 的 ORM 视图。

    ``_patch_run_context`` 走原生 ``text()`` UPDATE，SQLAlchemy 不会同步 identity
    map；而调用方手里的 ``run_ctx`` 只是 ``dict(pr.run_context or {})`` 的**副本**，
    写回它并不会改到 ORM 属性。因此**任何读 ``pr.run_context`` 的后续逻辑之前**
    必须 expire，否则读到的是写入前的陈旧 dict——聚合的 ``_abort_requested`` 正是
    这样的读者，漏掉它会让 abort 覆盖不生效（卡在 RUNNING 且 job 已全终态的 run
    会被判成 SUCCESS）。"""
    db.expire(pr, ["run_context"])


def abort_plan_run(
    plan_run_id: int,
    *,
    db: Session,
    reason: str = "aborted_by_user",
    triggered_by: Optional[str] = None,
    audit_user_id: Optional[int] = None,
    audit_username: Optional[str] = None,
    audit_action: str = "abort_plan_run",
    host_id: Optional[str] = None,
) -> dict:
    """Abort a PlanRun, or only the active jobs on one host.

    When ``host_id`` is ``None`` (default), aborts the entire PlanRun: all
    active jobs, whole-plan precheck/queued failure, and terminalization when
    no jobs remain.

    When ``host_id`` is set (host hot-update via :func:`abort_jobs_for_host`):
    only PENDING/RUNNING jobs bound to that host are aborted or signalled
    (**host scope**) — the control fan-out, the PENDING terminalization and
    ``abort_requested.requested_job_ids`` all cover that host only, and the
    abort reaper
    (:func:`backend.scheduler.device_lease_reconciler._reconcile_aborted_running_jobs`)
    reaps **only jobs present in ``requested_job_ids``** (#2050), so other hosts'
    RUNNING jobs keep running.  ``requested_job_ids`` carried by earlier aborts is
    preserved (merged, not replaced).  The in-precheck whole-plan FAILED path is
    not taken.

    ADR-0043（Accepted v1.1，#2154；下方原 #1928 注记**已失效**）：宽限的
    **计时主体 ≡ 请求主体**——``abort_requested`` 的 ``at`` / ``deadline_at`` 只由
    **run 级** abort 写入；host 级 abort 只维护该键的**名单语义**
    （``requested_job_ids`` 合并，#2050 候选收窄）与**存在性**（聚合 SUCCESS 污染 /
    热更新 ``abort_pending`` / recovery ``ABORT_LOCAL`` / dispatch 材料化保护这四类
    读者只看键是否存在），并把 host 主体的时钟写进
    ``abort_requested_hosts[host_id]``：**首次写入、后续不重置**（D2 —— 消除
    N×GRACE 放大与「最后写入者赢」）。reaper 按被回收 job 所属主体取时钟、两者
    并存时取更早者（D1）；host 级请求之后才被 claim 的该 host job 由 host 时钟
    覆盖（D3）；``abort_requested_hosts`` 缺失（历史 run_context）退化为 run 级
    时钟（D4）。

    Returns a summary dict::

        {
            "plan_run_id": int,
            "status": str,
            "aborted_jobs": [int, ...],
            "abort_requested_jobs": [int, ...],   # host 级 abort 时仅该主机的 job
            "phase": "precheck" | "running" | "queued",
        }

    Raises :class:`PlanRunAbortError` if the PlanRun is already in a
    terminal status.
    """
    # #1985：**先锁候选 PENDING job 行，再锁 plan_run** —— 与 complete / recycler
    # （PENDING 超时路径）/ reconciler / coordinator-heartbeat 的 job → plan_run 同序。
    # 原先顺序相反：本函数先锁 plan_run（下方 `lock_t0`），再在
    # `_bulk_abort_pending_jobs` 里 UPDATE 同一批 PENDING 行；而回收器的 PENDING
    # 超时路径先 `UPDATE job_instance … status='PENDING'` 锁 job，再在
    # `plan_aggregator_sync` 里锁 plan_run —— 两者争用同一行即成环。
    # 这里只把 abort 本来就会锁的同一批行**提前**按 id 升序锁住；下方所有逻辑
    # （终端态复检、`WHERE status='PENDING'`、计数器、聚合、#1552 的 run_context
    # 同步）一律不变，因此不改变 abort 的判定与语义。
    _candidate_filters = [
        JobInstance.plan_run_id == plan_run_id,
        JobInstance.status == JobStatus.PENDING.value,
    ]
    if host_id is not None:
        # host 级 abort 只需该 host 的候选，避免扩大锁面。
        _candidate_filters.append(JobInstance.host_id == host_id)
    db.execute(
        select(JobInstance.id)
        .where(*_candidate_filters)
        .order_by(JobInstance.id)
        .with_for_update()
    ).all()

    # Why: abort 也会写 pr.status,与 aggregator 并发时若不持锁会出现
    #      "aggregator 先 commit SUCCESS → abort 用 stale RUNNING 视图绕过
    #      aggregation guard,把状态改回 FAILED" 的覆盖。锁与 aggregator 同列。
    #      FOR NO KEY UPDATE 与 FK 触发的 FOR KEY SHARE 兼容,避免与 complete_job 的
    #      job UPDATE autoflush 死锁(见 aggregator.py 详细注释)。
    lock_t0 = time.perf_counter()
    pr = db.execute(
        select(PlanRun)
        .where(PlanRun.id == plan_run_id)
        .with_for_update(key_share=True)  # SQLAlchemy key_share → PG FOR NO KEY UPDATE (#1473)
    ).scalar_one_or_none()
    if pr is None:
        raise PlanRunAbortError(f"PlanRun {plan_run_id} not found")

    if pr.status in _TERMINAL_PLAN_RUN_STATUSES:
        raise PlanRunAbortError(
            f"PlanRun {plan_run_id} is already terminal: {pr.status}"
        )

    run_ctx = dict(pr.run_context or {})

    # ── ADR-0026: QUEUED / PRECHECK abort — no jobs exist (invariant ①),
    # so there is nothing to recycle: transition straight to FAILED.
    # Unreachable with the feature flag off (nothing produces these states).
    # Host-scoped abort must not terminalize the whole PlanRun (#1880).
    if (
        host_id is None
        and pr.status in (PlanRunStatus.QUEUED.value, PlanRunStatus.PRECHECK.value)
    ):
        now = datetime.now(timezone.utc)
        stray_jobs = (
            db.query(JobInstance.id)
            .filter(JobInstance.plan_run_id == plan_run_id)
            .first()
        )
        if stray_jobs is not None:
            # Invariant ① violated — do NOT silently terminalize over live
            # jobs; surface loudly and fall through to the standard path.
            logger.error(
                "abort_queued_plan_run_has_jobs plan_run=%d status=%s — "
                "invariant ① violated, falling back to standard abort",
                plan_run_id, pr.status,
            )
        else:
            phase = "queued" if pr.status == PlanRunStatus.QUEUED.value else "precheck"
            run_ctx["abort_requested"] = {
                "at": now.isoformat(),
                "reason": reason,
                "triggered_by": triggered_by,
                "requested_job_ids": [],
                "acknowledged_job_ids": [],
            }
            PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason=reason)
            pr.ended_at = now
            pr.result_summary = {
                "aborted": True,
                "reason": reason,
                "phase": phase,
                "total": 0,
            }
            _patch_run_context(
                db, plan_run_id, ["abort_requested"], run_ctx["abort_requested"],
            )
            record_audit(
                db,
                action=audit_action,
                resource_type="plan_run",
                resource_id=plan_run_id,
                details={"reason": reason, "phase": phase},
                user_id=audit_user_id,
                username=audit_username or triggered_by,
            )
            db.commit()
            record_plan_run_abort_lock_seconds(
                time.perf_counter() - lock_t0, "admission",
            )
            logger.info(
                "plan_run_abort_admission_queue plan_run=%d phase=%s", plan_run_id, phase,
            )
            notify_plan_run_terminal(
                pr,
                new_status=PlanRunStatus.FAILED,
                error_message=f"aborted ({reason}): phase={phase}",
            )
            return {
                "plan_run_id": plan_run_id,
                "status": PlanRunStatus.FAILED.value,
                "aborted_jobs": [],
                "phase": phase,
            }

    precheck = run_ctx.get("precheck") or None

    # Detect "still in dispatch gate, no jobs yet".
    # Host-scoped abort skips whole-plan precheck failure (#1880).
    in_precheck = (
        host_id is None
        and precheck is not None
        and precheck.get("phase") in ("verifying", "syncing", "reverifying")
    )

    aborted_jobs: list[int] = []
    abort_requested_jobs: list[int] = []
    abort_jobs_by_host: dict[str, list[int]] = defaultdict(list)
    abort_hosts: set[str] = set()
    # Aggregation / on_job_terminal already notify; only direct FAILED
    # transitions in this function should emit once more.
    direct_failed_notify = False

    if not in_precheck:
        # #703：只取 PENDING/RUNNING 的 id+host+status，勿 ``.all()`` 整行 ORM。
        # #327（~497 RUNNING）全量加载会拉长 FOR NO KEY UPDATE 持锁窗口，
        # 期间并发 complete_job 排队占满 QueuePool。
        active_rows = db.execute(
            select(JobInstance.id, JobInstance.host_id, JobInstance.status).where(
                JobInstance.plan_run_id == plan_run_id,
                JobInstance.status.in_(_ACTIVE_JOB_STATUSES),
            )
        ).all()
        if host_id is not None:
            scoped_rows = [row for row in active_rows if row.host_id == host_id]
            if not scoped_rows:
                record_plan_run_abort_lock_seconds(
                    time.perf_counter() - lock_t0, "noop",
                )
                return {
                    "plan_run_id": plan_run_id,
                    "status": pr.status,
                    "phase": "running",
                    "aborted_jobs": [],
                    "abort_requested_jobs": [],
                }
            active_rows = scoped_rows

        existing_abort = run_ctx.get("abort_requested") or {}
        existing_requested: list[int] = []
        existing_ack: list[int] = []
        if isinstance(existing_abort, dict):
            existing_requested = list(existing_abort.get("requested_job_ids") or [])
            existing_ack = list(existing_abort.get("acknowledged_job_ids") or [])

        now = datetime.now(timezone.utc)
        pending_ids = [
            row.id for row in active_rows if row.status == JobStatus.PENDING.value
        ]
        pending_host_by_id = {
            row.id: row.host_id
            for row in active_rows
            if row.status == JobStatus.PENDING.value
        }
        for row in active_rows:
            if row.status != JobStatus.RUNNING.value:
                continue
            abort_requested_jobs.append(row.id)
            if row.host_id:
                abort_hosts.add(row.host_id)
                abort_jobs_by_host[row.host_id].append(row.id)

        if host_id is not None:
            merged_requested = list(
                dict.fromkeys(existing_requested + abort_requested_jobs)
            )
            # ADR-0043 D1：host 级 abort **不写 run 级 `at`**——run 级 `at` 是 run
            # 主体的时钟，只由下方 run 级分支写入。此处只维护名单语义与键的存在性；
            # `{**existing_abort}` 会带上既有 run 级 `at`（若此前发生过 run 级
            # abort），但绝不覆盖/重置它（D2）。
            abort_requested_payload = {
                **(existing_abort if isinstance(existing_abort, dict) else {}),
                "reason": reason,
                "triggered_by": triggered_by,
                "requested_job_ids": merged_requested,
                "acknowledged_job_ids": existing_ack,
            }
        else:
            abort_requested_payload = {
                "at": now.isoformat(),
                "reason": reason,
                "triggered_by": triggered_by,
                "deadline_at": (
                    now + timedelta(seconds=ABORT_ACK_GRACE_SECONDS)
                ).isoformat(),
                "requested_job_ids": list(abort_requested_jobs),
                "acknowledged_job_ids": [],
            }
        run_ctx["abort_requested"] = abort_requested_payload
        # #1928 注记（**已失效**，ADR-0043 v1.1 / #2154）：本写入**曾**重置 run 级
        # `at`/`deadline_at`，使每次 host 级 abort 都把整轮宽限延长为「本次请求 +
        # GRACE」（最多多等 host 数 × GRACE）。现在 host 主体的计时落在下方
        # `abort_requested_hosts[host_id]`，run 级 `at` 只由 run 级 abort 写入。
        # #793：分段写（整段写回会覆盖并发写者如 archive/dispatch_state 的键）
        _patch_run_context(
            db, plan_run_id, ["abort_requested"], run_ctx["abort_requested"],
        )

        if host_id is not None:
            # ADR-0043 D1/D2：host 主体的宽限时钟——**首次写入、后续不重置**
            # （自首次请求起算）。重复请求只刷新 reason/triggered_by 便于审计，
            # `at` / `deadline_at` 一律沿用首次值。
            existing_host_clock = None
            existing_hosts = run_ctx.get("abort_requested_hosts")
            if isinstance(existing_hosts, dict):
                candidate = existing_hosts.get(host_id)
                if isinstance(candidate, dict):
                    existing_host_clock = candidate
            if existing_host_clock and existing_host_clock.get("at"):
                host_clock_payload = {
                    **existing_host_clock,
                    "reason": reason,
                    "triggered_by": triggered_by,
                }
            else:
                host_clock_payload = {
                    "at": now.isoformat(),
                    "reason": reason,
                    "triggered_by": triggered_by,
                    "deadline_at": (
                        now + timedelta(seconds=ABORT_ACK_GRACE_SECONDS)
                    ).isoformat(),
                }
            if not isinstance(run_ctx.get("abort_requested_hosts"), dict):
                run_ctx["abort_requested_hosts"] = {}
            run_ctx["abort_requested_hosts"][host_id] = host_clock_payload
            # jsonb_set 不创建父键 → 先确保 `abort_requested_hosts` 存在
            _ensure_abort_hosts_key(db, plan_run_id)
            _patch_run_context(
                db,
                plan_run_id,
                ["abort_requested_hosts", host_id],
                host_clock_payload,
            )

        # #1552：在**任何**读 pr.run_context 的后续逻辑之前同步 ORM 视图。
        _reload_run_context(db, pr)

        # #703：PENDING 批量终态化前先 commit，释放 PlanRun 行锁。
        # PENDING 风暴（#492/#246 ~359）若与 abort_requested 同事务，
        # complete_job 会在锁上排队并各占一条池连接 → QueuePool 耗尽。
        if pending_ids:
            db.commit()
            record_plan_run_abort_lock_seconds(
                time.perf_counter() - lock_t0, "abort_requested",
            )
            # #2012：上面的 COMMIT 释放 PlanRun 行锁的同时**也释放了函数开头
            # 预锁的 PENDING job 行**——若直接重锁 plan_run，本函数在
            # [commit, bulk UPDATE] 窗口又回到 plan_run → job 反序，与 recycler
            # PENDING 超时路径（持 job 行 → plan_aggregator_sync 再锁 plan_run）
            # 依旧成环：#1985 的预锁只覆盖了 commit 之前的分段。按同一 id 升序
            # 对同一批 `pending_ids` 重发预锁，使**全函数**保持 job → plan_run；
            # 锁面与开头预锁相同（含已离开 PENDING 的行，`_bulk_abort_pending_jobs`
            # 的 `WHERE status='PENDING'` 会照常跳过），持锁窗口到 finalize commit。
            db.execute(
                select(JobInstance.id)
                .where(JobInstance.id.in_(pending_ids))
                .order_by(JobInstance.id)
                .with_for_update()
            ).all()
            lock_t0 = time.perf_counter()
            pr = db.execute(
                select(PlanRun)
                .where(PlanRun.id == plan_run_id)
                .with_for_update(key_share=True)
            ).scalar_one()
            if pr.status in _TERMINAL_PLAN_RUN_STATUSES:
                # 并发路径已终态化；仍下发已记录的 abort control。
                pending_ids = []
            else:
                run_ctx = dict(pr.run_context or {})

        # #492: PENDING 批量终态化——单条 UPDATE 完成状态迁移 + 计数器
        # 一次聚合 + 聚合审计。旧实现逐条 transition+on_job_terminal_sync：
        # 246 的 359 台 PENDING 产生每秒 ~40 条 audit/UPDATE 风暴（手动 abort
        # 实测控制面卡顿、会话失效）。批量语义与逐条等价：
        #   - WHERE status='PENDING' 即状态机校验（同 VALID_TRANSITIONS 约束）
        #   - 计数器按 RETURNING 实际行数 +n（run 级与每 host 级；#988）
        #   - 审计聚合为一条 batch 记录
        #   - 批量块末尾调用一次计数器聚合（等价逐条路径的每次尝试）
        # #988: 预读 PENDING 后 Agent 可能 claim→RUNNING；UPDATE 的 WHERE 会跳过，
        # 但不得用预读 len 记账，也不得漏掉对这些 Job 的 abort 控制下发。
        if pending_ids:
            aborted_ids = _bulk_abort_pending_jobs(
                db,
                pr,
                plan_run_id,
                pending_ids,
                pending_host_by_id,
                reason=reason,
                triggered_by=triggered_by,
                now=now,
            )
            aborted_jobs.extend(aborted_ids)

            # 竞态 claim：预读为 PENDING、UPDATE 未命中 → 刷新后按 RUNNING 走停止协议
            raced_ids = set(pending_ids) - set(aborted_ids)
            for jid in raced_ids:
                job = db.get(JobInstance, jid)
                if job is None:
                    continue
                db.refresh(job)
                if job.status != JobStatus.RUNNING.value:
                    continue
                abort_requested_jobs.append(job.id)
                if job.host_id:
                    abort_hosts.add(job.host_id)
                    abort_jobs_by_host[job.host_id].append(job.id)

            # Refresh requested_job_ids only (pending jobs are already terminal).
            # #1924：与**锁内现值** merge——#703 的放锁窗口里并发 host 级 abort
            # 可能已按 "merged, not replaced" 契约并入条目（本函数 docstring），
            # 任一分支整写快照都会把它覆盖掉。re-lock 后 run_ctx 是新鲜读，
            # merge 只增不删，语义安全；host 分支此前用的 existing_requested
            # 是放锁前的旧读，同样有窗口，一并改为现值。
            current_requested = list(
                (run_ctx.get("abort_requested") or {}).get("requested_job_ids")
                or []
            )
            refreshed_requested = list(
                dict.fromkeys(current_requested + abort_requested_jobs)
            )
            run_ctx.setdefault("abort_requested", {})
            if isinstance(run_ctx.get("abort_requested"), dict):
                run_ctx["abort_requested"]["requested_job_ids"] = refreshed_requested
            _patch_run_context(
                db,
                plan_run_id,
                ["abort_requested", "requested_job_ids"],
                refreshed_requested,
            )
            # 同 #1552：第二次分段写之后再次同步（下方兜底聚合同样读 pr.run_context）
            _reload_run_context(db, pr)

        # 是否还有活跃 job：EXISTS，避免再装载全表 ORM。
        has_active = db.execute(
            select(JobInstance.id).where(
                JobInstance.plan_run_id == plan_run_id,
                JobInstance.status.in_(_ACTIVE_JOB_STATUSES),
            ).limit(1)
        ).first()
        if has_active is None and pr.status not in _TERMINAL_PLAN_RUN_STATUSES:
            total = int(pr.total_job_count or 0)
            if total > 0:
                apply_plan_run_aggregation_from_counters(pr)
            else:
                # legacy total_job_count==0：才回退全量扫描。
                all_jobs = (
                    db.query(JobInstance)
                    .filter(JobInstance.plan_run_id == plan_run_id)
                    .all()
                )
                if all_jobs:
                    apply_plan_run_aggregation(pr, all_jobs)
                elif host_id is None:
                    PlanRunStateMachine.transition(
                        pr, PlanRunStatus.FAILED, reason=reason,
                    )
                    pr.ended_at = now
                    pr.result_summary = {
                        "aborted": True,
                        "reason": reason,
                        "empty_run": True,
                    }
                    direct_failed_notify = True
    # In-precheck path: no jobs to release; we close the PlanRun directly.
    now_iso = datetime.now(timezone.utc).isoformat()
    if in_precheck:
        precheck["phase"] = "failed"
        precheck["final_result"] = "aborted"
        precheck["completed_at"] = now_iso
        precheck.setdefault("errors", []).append(f"aborted: {reason}")
        run_ctx["precheck"] = precheck
        PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason=reason)
        pr.ended_at = datetime.now(timezone.utc)
        pr.result_summary = {
            "precheck_failed": True,
            "reason": reason,
            "aborted": True,
        }
        direct_failed_notify = True

    if in_precheck:
        # #793：precheck 亦分段写（同一行可能被归档等并发写者更新）
        _patch_run_context(db, plan_run_id, ["precheck"], precheck)
    # 不再整段写回；precheck 分支的写同样要同步 ORM 视图（#1552）。
    # 注意：读取方必须在此之前——abort 主路径的聚合读取已在上方各自就位。
    _reload_run_context(db, pr)
    db.flush()

    record_audit(
        db,
        action=audit_action,
        resource_type="plan_run",
        resource_id=plan_run_id,
        details={
            "reason": reason,
            "phase": "precheck" if in_precheck else "running",
            "aborted_jobs": aborted_jobs,
            "abort_requested_jobs": abort_requested_jobs,
            "triggered_by": triggered_by,
        },
        user_id=audit_user_id,
        username=audit_username,
    )
    db.commit()
    record_plan_run_abort_lock_seconds(
        time.perf_counter() - lock_t0, "finalize",
    )

    if direct_failed_notify:
        notify_plan_run_terminal(
            pr,
            new_status=PlanRunStatus.FAILED,
            error_message=(
                f"aborted ({reason}): {len(aborted_jobs)} jobs terminated"
            ),
        )

    # Non-blocking control delivery.  The lease remains ACTIVE until the Agent
    # acknowledges termination, so a lost command cannot make the device
    # schedulable while the old process is still running.
    # #703：host 扇出合并为单次 schedule_agent_control_fanout，避免上百次
    # run_coroutine_threadsafe 同步灌满主事件循环。
    control_items: list[tuple[str, dict]] = []
    for emit_host_id in abort_hosts:
        host_job_ids = abort_jobs_by_host.get(emit_host_id, [])
        if not host_job_ids:
            continue
        control_items.append(
            (
                emit_host_id,
                {
                    "command": "abort",
                    "payload": {
                        "plan_run_id": plan_run_id,
                        "job_ids": host_job_ids,
                        "reason": reason,
                    },
                },
            )
        )
    schedule_agent_control_fanout(control_items)

    # ADR-0025 Sprint 4: abort 导致 PlanRun 终态时触发归档-2 scan + merge
    if should_trigger_dedup(pr.status):
        enqueue_dedup_terminal_sync(plan_run_id)

    # ── SocketIO push ──
    # #703：大批量 abort 不再逐 job 推 JOB_STATUS。#327（~497 job / 30 host）
    # 实测每个 schedule_emit → run_coroutine_threadsafe，数百次同步投递打满
    # 事件循环 → ASGI/DB 拿不到调度 → refresh 失败被前端误踢登录。
    # PLAN_RUN_STATUS 已覆盖详情页 devices/timeline/logs 全量 invalidate；
    # 另发一条汇总 JOB_STATUS 触发 dashboard results 节流刷新（与逐条等价语义）。
    ts = datetime.now(timezone.utc).isoformat()
    room = f"plan_run:{plan_run_id}"
    if aborted_jobs or abort_requested_jobs:
        schedule_emit(
            "job_status",
            {
                "type": "JOB_STATUS",
                "payload": {
                    "plan_run_id": plan_run_id,
                    "status": "ABORTED" if aborted_jobs else "RUNNING",
                    "abort_bulk": True,
                    "aborted_count": len(aborted_jobs),
                    "abort_requested_count": len(abort_requested_jobs),
                    "reason": reason,
                },
                "timestamp": ts,
            },
            namespace="/dashboard",
            room=room,
        )
    schedule_emit(
        "plan_run_status",
        {
            "type": "PLAN_RUN_STATUS",
            "payload": {
                "plan_run_id": plan_run_id,
                "status": pr.status,
            },
            "timestamp": ts,
        },
        namespace="/dashboard",
        room=room,
    )

    db.refresh(pr)

    logger.info(
        "plan_run_aborted plan_run=%d phase=%s aborted_jobs=%d",
        plan_run_id,
        "precheck" if in_precheck else "running",
        len(aborted_jobs),
    )

    return {
        "plan_run_id": plan_run_id,
        "status": pr.status,
        "phase": "precheck" if in_precheck else "running",
        "aborted_jobs": aborted_jobs,
        "abort_requested_jobs": abort_requested_jobs,
    }


def abort_jobs_for_host(
    host_id: str,
    *,
    db: Session,
    reason: str = "aborted_for_host_update",
    triggered_by: Optional[str] = None,
    audit_user_id: Optional[int] = None,
    audit_username: Optional[str] = None,
) -> dict:
    """Abort every active Job (PENDING/RUNNING) currently bound to ``host_id``.

    Walks the affected PlanRuns once each (deduped) and calls
    :func:`abort_plan_run` with ``host_id`` so only that host's jobs are
    aborted; other hosts on the same PlanRun keep running (#1880).
    Uses ``audit_action='abort_jobs_for_host_update'``.

    Returns aggregate counts across PlanRuns::

        {
            "host_id": "...",
            "plan_runs": [int, ...],
            "aborted_jobs": [int, ...],
        }
    """
    active_jobs = (
        db.query(JobInstance.plan_run_id)
        .filter(
            JobInstance.host_id == host_id,
            JobInstance.status.in_(
                [JobStatus.PENDING.value, JobStatus.RUNNING.value]
            ),
        )
        .distinct()
        .all()
    )
    plan_run_ids = sorted({row[0] for row in active_jobs if row[0] is not None})

    aggregate_aborted: list[int] = []
    for prid in plan_run_ids:
        try:
            summary = abort_plan_run(
                prid,
                db=db,
                reason=reason,
                triggered_by=triggered_by,
                audit_user_id=audit_user_id,
                audit_username=audit_username,
                audit_action="abort_jobs_for_host_update",
                host_id=host_id,
            )
        except PlanRunAbortError as exc:
            # #2012（连带）：异常路径可能已持有开头预锁的 PENDING job 行与
            # plan_run 行锁（如并发窗口内撞上已终态的复检 raise）。本函数把该
            # 异常当「可跳过」继续处理同 host 的其余 run，且同一 session 随后
            # 会被热更新流程复用（drain 轮询 + SSH 部署，分钟级）——不回滚会把
            # 这些行锁带满整个窗口，阻塞 complete_job / recycler（claim 的
            # SKIP LOCKED 会静默让路，等待观测面也看不出）。回滚释放行锁，
            # 对后续迭代无影响（plan_run_ids 在循环前已物化）。
            db.rollback()
            logger.warning(
                "abort_jobs_for_host_skip plan_run=%d host=%s: %s",
                prid, host_id, exc,
            )
            continue
        aggregate_aborted.extend(summary["aborted_jobs"])

    return {
        "host_id": host_id,
        "plan_runs": plan_run_ids,
        "aborted_jobs": aggregate_aborted,
    }
