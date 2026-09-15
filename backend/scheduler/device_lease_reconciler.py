"""Device Lease Reconciler — ADR-0019 Phase 4a/4b.

The **sole handler** of lease expiration.  Replaces watchdog's
``_check_device_lock_expiration`` which is now disabled.

Two-phase lease expiry:
  Phase 1: RUNNING + expired-ACTIVE-lease → UNKNOWN (lease stays ACTIVE, device blocked)
  Phase 2: UNKNOWN + grace expired → release_lease + FAILED

Also handles:
  - Stale UNKNOWN jobs whose lease is already gone
  - Terminal jobs with lingering ACTIVE leases (D5)

Entry point: ``device_lease_reconcile_once()`` invoked by APScheduler
IntervalTrigger (see ``app_scheduler.py``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy.exc
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import TIMESTAMP

from backend.core.database import AsyncSessionLocal
from backend.core.metrics import (
    reconciler_runs,
    reconciler_actions,
)
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.aggregator import PlanAggregator
from backend.services.lease_manager import release_lease
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)

from backend.core.job_timeout_config import (
    ABORT_ACK_GRACE_SECONDS as _ABORT_REAPER_GRACE_SECONDS,
    UNKNOWN_GRACE_SECONDS as _UNKNOWN_GRACE_SECONDS,
)

# ── Phase 4b: terminal statuses for D5 cleanup.  Does NOT include UNKNOWN —
#    UNKNOWN must go through the grace-period branch.
_FINAL_STATUSES: set[str] = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Check 1: expired ACTIVE leases → UNKNOWN / FAILED
# ═══════════════════════════════════════════════════════════════════════════════

async def _reconcile_expired_leases(db) -> tuple[int, int, int]:
    """Process all expired ACTIVE JOB leases.

    Returns (unknown_count, failed_count, terminal_released_count).
    """
    now = datetime.now(timezone.utc)

    expired = (await db.execute(
        select(DeviceLease).where(
            DeviceLease.status == LeaseStatus.ACTIVE.value,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.expires_at < now,
        )
    )).scalars().all()

    # #1959 / #992: 全局一致锁序 —— 先 Job 再 Lease。
    # complete（``complete_job``）与 ``extend_leases_batch`` 都是 Job → Lease；
    # 本检查原先相反（先 ``FOR UPDATE`` DeviceLease，再 ``FOR UPDATE`` JobInstance），
    # 于是「批量续租 × 过期回收」在同一 (job, lease) 两行上形成环路等待，
    # PostgreSQL 反复检测到死锁（2026-09-13/14 观测 83 次，48 次卡在
    # ``job_instance`` 元组）。候选按 job_id 升序处理，与 ``extend_leases_batch``
    # 的 ``WHERE id IN (...) ORDER BY id FOR UPDATE``（``agent_api.py:1562`` 起）
    # 处于同一全序，避免跨候选再引入逆序；``job_id`` 为 NULL 的孤儿租约没有
    # Job 可取锁，排在最后。
    ordered = sorted(expired, key=lambda lease: (
        lease.job_id is None, lease.job_id or 0, lease.id,
    ))

    unknown_count = 0
    failed_count = 0
    terminal_released_count = 0
    # #1172: on_job_terminal 自管理提交（#986 契约：聚合后先提交父终态再
    # 触发链式派发）——不能在 begin_nested 内调用。savepoint 提交后由
    # 函数尾部统一终态化并返回；其余候选留待下轮 tick。
    terminalize: JobInstance | None = None

    for candidate in ordered:
        try:
            async with db.begin_nested():
                # 锁序：Job → Lease（见上方 #1959 说明）。candidate 来自预扫描，
                # 其 job_id 即该行的真实归属——``acquire_lease`` 只 INSERT 新行
                # （``lease_manager.py:126`` 起），租约行的 job_id 不会换绑，
                # 因此可以据此在锁 Lease 之前先锁 Job。
                job = None
                if candidate.job_id is not None:
                    try:
                        job = (await db.execute(
                            select(JobInstance)
                            .where(JobInstance.id == candidate.job_id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )).scalars().first()
                    except Exception:
                        logger.warning(
                            "reconciler_job_load_failed job=%s", candidate.job_id,
                            exc_info=True,
                        )
                        continue

                lease = (await db.execute(
                    select(DeviceLease)
                    .where(DeviceLease.id == candidate.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )).scalars().first()
                if (
                    lease is None
                    or lease.status != LeaseStatus.ACTIVE.value
                    or lease.expires_at >= now
                ):
                    continue
                job_id = lease.job_id
                device_id = lease.device_id

                if candidate.job_id != job_id:
                    # 理论上不可达：租约行只 INSERT、不换绑 job（acquire_lease）。
                    # 一旦出现，说明预扫描与锁内行已不同源——保守跳过，交下一轮，
                    # 以免在未持该 Job 锁时触碰 Lease 而破坏刚建立的锁序。
                    logger.warning(
                        "reconciler_lease_job_mismatch lease=%s scanned_job=%s locked_job=%s",
                        candidate.id, candidate.job_id, job_id,
                    )
                    continue

                if job_id is None:
                    # Orphan lease (no associated job) — release it directly
                    lease.status = LeaseStatus.RELEASED.value
                    lease.released_at = now
                    logger.warning(
                        "reconciler_orphan_lease_released device=%s job=None", device_id,
                    )
                    terminal_released_count += 1
                    continue

                if job is None:
                    # Orphan lease (job deleted, but FK should prevent this) — release it.
                    # 没有 Job 行可取锁，故不触及锁序不变量。
                    await release_lease(db, device_id, job_id, LeaseType.JOB)
                    logger.warning(
                        "reconciler_orphan_lease_released device=%s job=%s", device_id, job_id,
                    )
                    terminal_released_count += 1
                    continue

                if job.status in _FINAL_STATUSES:
                    # D5: terminal job with lingering ACTIVE lease
                    await release_lease(db, device_id, job_id, LeaseType.JOB)
                    logger.warning(
                        "reconciler_terminal_job_active_lease device=%s job=%s status=%s",
                        device_id, job_id, job.status,
                    )
                    terminal_released_count += 1
                    continue

                if job.status == JobStatus.RUNNING.value:
                    # Phase 1: RUNNING → UNKNOWN, keep lease ACTIVE (blocking)
                    try:
                        JobStateMachine.transition(job, JobStatus.UNKNOWN, "lease_expired")
                        job.ended_at = now  # REQUIRED: grace period & recovery depend on this
                        await db.flush()
                        logger.warning(
                            "reconciler_lease_expired_running_to_unknown device=%s job=%s",
                            device_id, job_id,
                        )
                        unknown_count += 1
                    except InvalidTransitionError:
                        logger.debug(
                            "reconciler_skip_invalid_transition device=%s job=%s status=%s",
                            device_id, job_id, job.status,
                        )
                    continue

                if job.status == JobStatus.UNKNOWN.value:
                    # Phase 2: UNKNOWN + grace expired → release + FAILED
                    grace_deadline = now - timedelta(seconds=_UNKNOWN_GRACE_SECONDS)
                    if job.ended_at and job.ended_at < grace_deadline:
                        await release_lease(db, device_id, job_id, LeaseType.JOB)

                        try:
                            JobStateMachine.transition(
                                job, JobStatus.FAILED, "unknown_grace_timeout",
                            )
                            await db.flush()
                            terminalize = job  # savepoint 提交后统一终态化
                        except InvalidTransitionError:
                            pass
                    # else: still within grace — do nothing
                    break  # 事务交由函数尾部终态化（#1172）

                # Other statuses (PENDING, etc.) — skip
        except Exception:
            logger.exception(
                "reconciler_expired_lease_failed lease=%s device=%s job=%s",
                candidate.id, candidate.device_id, candidate.job_id,
            )

    if terminalize is not None:
        await PlanAggregator.on_job_terminal(terminalize, db)
        failed_count += 1
        logger.warning(
            "reconciler_unknown_grace_released device=%s job=%s ended_at=%s",
            terminalize.device_id, terminalize.id, terminalize.ended_at,
        )

    return unknown_count, failed_count, terminal_released_count


# ═══════════════════════════════════════════════════════════════════════════════
# Check 2: stale UNKNOWN jobs whose lease is already gone
# ═══════════════════════════════════════════════════════════════════════════════

async def _reconcile_stale_unknown_jobs(db) -> int:
    """Finalize UNKNOWN jobs past grace whose ACTIVE lease has already
    been released (e.g. by watchdog before it was disabled, or by a prior
    Reconciler pass that failed to transition the job).
    """
    now = datetime.now(timezone.utc)
    grace_deadline = now - timedelta(seconds=_UNKNOWN_GRACE_SECONDS)

    stale = (await db.execute(
        select(JobInstance).where(
            JobInstance.status == JobStatus.UNKNOWN.value,
            JobInstance.ended_at.is_not(None),
            JobInstance.ended_at < grace_deadline,
        )
    )).scalars().all()

    failed = 0
    # #1172: on_job_terminal 自管理提交（#986 契约）——不在 begin_nested 内
    # 调用；savepoint 提交后由函数尾部统一终态化，其余候选下轮 tick 处理。
    terminalize: JobInstance | None = None
    for candidate in stale:
        try:
            async with db.begin_nested():
                job = (await db.execute(
                    select(JobInstance)
                    .where(JobInstance.id == candidate.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )).scalars().first()
                if (
                    job is None
                    or job.status != JobStatus.UNKNOWN.value
                    or job.ended_at is None
                    or job.ended_at >= grace_deadline
                ):
                    continue
                # If there's still an ACTIVE lease, release it
                active_lease = (await db.execute(
                    select(DeviceLease).where(
                        DeviceLease.device_id == job.device_id,
                        DeviceLease.job_id == job.id,
                        DeviceLease.lease_type == LeaseType.JOB.value,
                        DeviceLease.status == LeaseStatus.ACTIVE.value,
                    )
                )).scalars().first()

                if active_lease is not None:
                    await release_lease(db, job.device_id, job.id, LeaseType.JOB)

                try:
                    JobStateMachine.transition(
                        job, JobStatus.FAILED, "unknown_grace_timeout",
                    )
                    await db.flush()
                    terminalize = job
                except InvalidTransitionError:
                    pass
                break  # 事务交由函数尾部终态化（#1172）
        except Exception:
            logger.exception(
                "reconciler_stale_unknown_failed device=%s job=%s",
                candidate.device_id, candidate.id,
            )

    if terminalize is not None:
        await PlanAggregator.on_job_terminal(terminalize, db)
        failed += 1
        logger.warning(
            "reconciler_stale_unknown_finalized device=%s job=%s ended_at=%s",
            terminalize.device_id, terminalize.id, terminalize.ended_at,
        )

    return failed


# ═══════════════════════════════════════════════════════════════════════════════
# Check 3: terminal jobs with lingering ACTIVE leases (D5)
# ═══════════════════════════════════════════════════════════════════════════════

async def _reconcile_terminal_job_active_leases(db) -> int:
    """Release ACTIVE JOB leases for jobs that are already in a terminal state.

    Uses an explicit JOIN to find (lease, job) pairs where the lease is still
    ACTIVE but the job has finished.  Does NOT change the job status.
    """
    rows = (await db.execute(
        select(DeviceLease, JobInstance.status)
        .join(JobInstance, JobInstance.id == DeviceLease.job_id)
        .where(
            DeviceLease.status == LeaseStatus.ACTIVE.value,
            DeviceLease.lease_type == LeaseType.JOB.value,
            JobInstance.status.in_(_FINAL_STATUSES),
        )
    )).all()

    released = 0
    for lease, _job_status in rows:
        try:
            async with db.begin_nested():
                await release_lease(db, lease.device_id, lease.job_id, LeaseType.JOB)

                logger.warning(
                    "reconciler_terminal_job_active_lease_released device=%s job=%s status=%s",
                    lease.device_id, lease.job_id, _job_status,
                )
                released += 1
        except Exception:
            logger.exception(
                "reconciler_terminal_job_active_lease_failed lease=%s device=%s job=%s",
                lease.id, lease.device_id, lease.job_id,
            )

    return released


# ═══════════════════════════════════════════════════════════════════════════════
# Check 0: abort reaper — RUNNING + abort_requested + grace → UNKNOWN
# ═══════════════════════════════════════════════════════════════════════════════

_ABORT_REAPER_BROADCASTS: dict[str, list[dict]] = {}


async def _abort_reaper_recheck_job(
    db, job_id: int, now: datetime,
) -> tuple[bool, dict | None]:
    """Lock-reread one RUNNING+abort-requested candidate and recover it.

    R06-F04 (#989): the candidate scan above has already loaded the row into
    this session's identity map.  A plain ``SELECT ... FOR UPDATE`` on the
    same id returns that cached object *without refreshing its attributes*,
    so a terminal state committed by a concurrent ``/complete`` between the
    scan and the lock would be shadowed by the stale RUNNING view — the
    reaper would then write ABORTED/COMPLETED jobs back to UNKNOWN.
    ``populate_existing`` forces the locked re-read to refresh, and the
    re-check on fresh status is the conditional guard.

    Returns ``(changed, broadcast_item)`` — ``changed=False`` when the job is
    gone or no longer RUNNING (concurrent complete/abort won the race).
    """
    job = (await db.execute(
        select(JobInstance)
        .where(JobInstance.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().first()
    if job is None or job.status != JobStatus.RUNNING.value:
        return False, None
    try:
        JobStateMachine.transition(
            job, JobStatus.UNKNOWN, "abort_ack_timeout",
        )
    except InvalidTransitionError:
        logger.debug(
            "abort_reaper_skip_transition job=%d status=%s",
            job.id, job.status,
        )
        return False, None

    job.ended_at = now
    return True, {
        "type": "job_status",
        "job_id": job.id,
        "plan_run_id": job.plan_run_id,
        "status": "UNKNOWN",
        "plan_run_terminal": False,
    }


def _parse_abort_at(value: object) -> datetime | None:
    """把 ``run_context.abort_requested.at`` 解析为 aware datetime（#782）。

    容忍实际会出现的三种写法：``…Z`` / ``…+00:00`` / 带或不带小数秒。解析失败返回
    ``None``，调用方按「不满足回收条件」处理——坏值不触发 UNKNOWN 翻转，避免误杀。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        # 3.11 之前 fromisoformat 不认 'Z'，显式归一为 +00:00。
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # 无时区信息按平台约定视为 UTC（全平台时间戳均为 UTC）。
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _abort_at_expired(raw_abort_at: object, deadline: datetime) -> bool:
    """fallback 判据：abort 请求时刻早于 ``deadline``（Python 侧解析后比较）。"""
    parsed = _parse_abort_at(raw_abort_at)
    return parsed is not None and parsed < deadline


def _abort_request_covers_job(plan_run: PlanRun, job_id: int) -> bool:
    """该 job 是否在 ``abort_requested.requested_job_ids`` 之内（#2050）。

    host 级 abort（#1880 `abort_jobs_for_host` → `abort_plan_run(host_id=…)`）只把
    **该主机**的 RUNNING job 写进集合，但写的是 **run 级** `abort_requested.at`；
    本函数存在之前，reaper 的候选判据只有「存在 at + grace 已到」，于是同 run 上
    其他主机**从未被请求中止**的正常 job 也会在 grace 到期后被打成 UNKNOWN
    （`state_machine` 只允许 `UNKNOWN → {RUNNING, FAILED}`，迟到的 COMPLETED 也落
    FAILED，且 UNKNOWN 期间保留 ACTIVE lease 占着设备）——与
    `plan_run_abort.abort_plan_run` 的 docstring 契约相反。

    兼容性：**键缺失或非列表/空**（历史 run_context、异常形态）时不参与过滤，
    保持既有行为——否则老数据里未写集合的 run 会永远无人回收。
    """
    ctx = plan_run.run_context if isinstance(plan_run.run_context, dict) else {}
    abort = ctx.get("abort_requested")
    if not isinstance(abort, dict):
        return True
    requested = abort.get("requested_job_ids")
    if not isinstance(requested, list) or not requested:
        return True
    try:
        return job_id in {int(x) for x in requested}
    except (TypeError, ValueError):
        return True


async def _reconcile_aborted_running_jobs(db) -> tuple[int, list[dict]]:
    """P1: 扫描 RUNNING job 且 PlanRun.run_context 含 abort_requested 且
    grace 已到 → JobStateMachine.transition UNKNOWN，保留 ACTIVE lease 隔离
    设备。UNKNOWN grace 到期后再由标准 reconciler FAILED + release。

    候选面在时间判据之外还要求该 job **被请求过中止**
    （``abort_requested.requested_job_ids``，见 :func:`_abort_request_covers_job`）：
    host 级 abort 只请求该主机的 job，run 级 `at` 却对整轮成立（#2050）。
    集合缺失（历史数据）时退化为「只看 at」。

    返回 (aborted_count, broadcast_items) — 每项 dict:
        {type, job_id, plan_run_id, status, plan_run_terminal}
    """
    now = datetime.now(timezone.utc)
    grace_deadline = now - timedelta(seconds=_ABORT_REAPER_GRACE_SECONDS)

    # PG JSONB path extraction with timestamptz cast:
    #   run_context -> 'abort_requested' ->> 'at' → text
    #   ... ::timestamptz → native PG comparison against grace_deadline
    abort_at_text = PlanRun.run_context['abort_requested']['at'].astext
    try:
        rows = (await db.execute(
            select(JobInstance, PlanRun)
            .join(PlanRun, PlanRun.id == JobInstance.plan_run_id)
            .where(
                JobInstance.status == JobStatus.RUNNING.value,
                abort_at_text.isnot(None),
                abort_at_text.cast(TIMESTAMP(timezone=True)) < grace_deadline,
            )
        )).all()
    except (sqlalchemy.exc.DataError, sqlalchemy.exc.DBAPIError):
        logger.warning(
            "abort_reaper_timestamptz_cast_failed, fallback to python datetime compare"
        )
        # Rollback the failed transaction so the fallback query can execute.
        # Without this, PostgreSQL raises InFailedSQLTransactionError for any
        # subsequent statement in the same transaction.
        await db.rollback()
        # #782：不在 SQL 里比时间。ISO **文本**比较在形态混用时给出错误顺序——
        # 非零偏移（`+08:00`）、`Z` 与 `+00:00` 混用、小数秒有无都会改变字典序，
        # 可能与真实时间相反（例：`…T19:59:59+08:00` 早于 `…T12:00:00+00:00`，
        # 文本比较却判为不早），既可能漏回收也可能误回收。改为取回候选后在
        # Python 侧解析比较；候选面被 RUNNING + abort_requested 双重限定，行数有界。
        candidates = (await db.execute(
            select(JobInstance, PlanRun, abort_at_text)
            .join(PlanRun, PlanRun.id == JobInstance.plan_run_id)
            .where(
                JobInstance.status == JobStatus.RUNNING.value,
                abort_at_text.isnot(None),
            )
        )).all()
        rows = [
            (job, plan_run)
            for job, plan_run, raw_abort_at in candidates
            if _abort_at_expired(raw_abort_at, grace_deadline)
        ]

    # #2050：时间判据之外再要求「该 job 被请求过中止」——host 级 abort 只请求该
    # 主机的 job，而 run 级 `at` 对整轮成立；不过滤会把同 run 其他主机的正常 job
    # 一并打成 UNKNOWN。集合缺失（历史 run_context）时本过滤不生效。
    rows = [
        (job, plan_run) for job, plan_run in rows
        if _abort_request_covers_job(plan_run, job.id)
    ]

    unknown_count = 0
    broadcast_items: list[dict] = []

    for candidate, _pr in rows:
        try:
            async with db.begin_nested():
                changed, item = await _abort_reaper_recheck_job(
                    db, candidate.id, now,
                )
                if not changed:
                    continue

                unknown_count += 1
                broadcast_items.append(item)

                logger.warning(
                    "abort_reaper job=%d plan_run=%d -> UNKNOWN (lease retained)",
                    candidate.id, candidate.plan_run_id,
                )
        except Exception:
            logger.exception(
                "abort_reaper_failed job=%d plan_run=%d",
                candidate.id, candidate.plan_run_id,
            )

    return unknown_count, broadcast_items


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

# Phase 6d: _fallback_release_lease removed — release_lease is now a single
# UPDATE without projection writes, so no fallback path is needed.


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

_reconcile_lock = asyncio.Lock()


async def device_lease_reconcile_once() -> None:
    """Run all reconciler checks in a fixed order.

    Each check runs in its own transaction: commit on success, rollback on
    failure.  One check failing does not block the next.

    Guarded by an asyncio.Lock to prevent concurrent execution when a prior
    cycle runs longer than the APScheduler interval.
    """
    if _reconcile_lock.locked():
        logger.warning("reconciler_skip_previous_still_running")
        _record_check("concurrent_skip", "skipped", None)
        return

    async with _reconcile_lock:
        await _reconcile_checks()


async def _reconcile_checks() -> None:
    from backend.realtime.socketio_server import (
        broadcast_plan_run_status,
        broadcast_run_job_update,
    )

    # has_broadcast=False 的三条路径终态化 PlanRun 时不广播 plan_run_status：
    # 有意取舍——权威（plan_run.status）不失真，页面靠前端 10s/30s 轮询兜底收敛
    # （成文见 docs/design/06-realtime-and-background.md §3「回收路径的页面收敛」）。
    # 补广播属行为变更，需单独评审，勿顺手改 True。
    checks: list[tuple[str, callable, bool]] = [
        ("aborted_running_jobs", _reconcile_aborted_running_jobs, True),  # P1: 优先级最高
        ("expired_leases", _reconcile_expired_leases, False),
        ("stale_unknown", _reconcile_stale_unknown_jobs, False),
        ("terminal_job_active_lease", _reconcile_terminal_job_active_leases, False),
    ]

    for label, check_fn, has_broadcast in checks:
        async with AsyncSessionLocal() as db:
            try:
                result = await check_fn(db)
                await db.commit()
                _record_check(label, "success", result)

                # P1 reaper: commit 后 broadcast
                if has_broadcast and result is not None:
                    _aborted_count, broadcast_items = result
                    for item in broadcast_items:
                        await broadcast_run_job_update(
                            item["plan_run_id"],
                            item["job_id"],
                            item["status"],
                        )
                        if item.get("plan_run_terminal"):
                            pr = await db.get(PlanRun, item["plan_run_id"])
                            if pr is not None:
                                await broadcast_plan_run_status(
                                    pr.id, pr.status,
                                )
            except Exception:
                await db.rollback()
                logger.exception("reconciler_check_failed check=%s", label)
                _record_check(label, "error", None)


def _record_check(label: str, outcome: str, result) -> None:
    """Record reconciler metrics for a single check."""
    try:
        reconciler_runs.labels(check=label, outcome=outcome).inc()

        if label == "aborted_running_jobs" and result is not None:
            unknown_count, _broadcast_items = result
            if unknown_count:
                reconciler_actions.labels(
                    action="to_unknown", reason="abort_ack_timeout",
                ).inc(unknown_count)
        elif label == "expired_leases" and result is not None:
            unknown, failed, terminal = result
            if unknown:
                reconciler_actions.labels(action="to_unknown", reason="lease_expired").inc(unknown)
            if failed:
                reconciler_actions.labels(action="to_failed", reason="unknown_grace_timeout").inc(failed)
            if terminal:
                reconciler_actions.labels(
                    action="release_lease", reason="terminal_job_active_lease"
                ).inc(terminal)
        elif label == "stale_unknown" and result:
            reconciler_actions.labels(action="to_failed", reason="unknown_grace_timeout").inc(result)
        elif label == "terminal_job_active_lease" and result:
            reconciler_actions.labels(
                action="release_lease", reason="terminal_job_active_lease"
            ).inc(result)
    except Exception:
        logger.debug("reconciler_metrics_failed", exc_info=True)
