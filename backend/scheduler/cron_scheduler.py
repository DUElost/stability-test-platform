# -*- coding: utf-8 -*-
"""
Cron Schedule Checker — polls task_schedules and dispatches PlanRuns.

Refactored for APScheduler 4.x: the CronScheduler daemon thread has been
replaced by two standalone functions invoked via APScheduler IntervalTrigger:

- ``check_and_fire_schedules()``  — async, called every _sched().cron_poll_interval
- ``run_retention_cleanup()``     — sync, called every hour
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, or_, select

from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.core.metrics import record_retention_candidates, record_retention_txn
from backend.models.enums import PlanRunStatus
from backend.models.schedule import TaskSchedule, schedule_timestamp

from backend.core.settings.scheduler import get_scheduler_settings


def _sched():
    """调度域 Settings 惰性取值（ADR-0042 P1 试点）。"""
    return get_scheduler_settings()


logger = logging.getLogger(__name__)



def _compute_next_run(cron_expression: str, after: datetime) -> datetime:
    """Compute next run time using croniter."""
    from croniter import croniter
    cron = croniter(cron_expression, after)
    return schedule_timestamp(cron.get_next(datetime))


def _next_schedule_run(cron_expression: str, after: datetime) -> datetime:
    return schedule_timestamp(_compute_next_run(cron_expression, after))


async def _dispatch_plan_async(
    plan_id: int, device_ids: list, db, *, schedule_id: int,
) -> None:
    """Dispatch a Plan using the provided async session (ADR-0020).

    schedule_id 写入 PlanRun.run_context 以便后续窗口去重查询。
    """
    from backend.services.plan_dispatcher import dispatch_plan, PlanDispatchError
    try:
        await dispatch_plan(
            plan_id=plan_id,
            device_ids=device_ids,
            triggered_by="cron",
            db=db,
            run_type="SCHEDULE",
            run_context={"schedule_id": schedule_id},
        )
    except PlanDispatchError as exc:
        logger.error("cron_dispatch_plan_error plan_id=%s: %s", plan_id, exc)


async def _recently_triggered_by_schedule(
    db, schedule_id: int, since: datetime,
) -> bool:
    """ADR-0020 schedule 抖动去重：同一 schedule 在 *since* 之后是否已产出 root PlanRun。

    通过 ``run_context @> {"schedule_id": <id>}``（PostgreSQL JSONB containment）
    + 仅匹配 root（``parent_plan_run_id IS NULL``）实现。SQLite 测试场景下
    ``run_context`` 可能是 TEXT 列，回退到字符串包含检查（仅用于测试）。
    """
    from backend.models.plan_run import PlanRun
    from sqlalchemy import text

    bind = db.get_bind() if hasattr(db, "get_bind") else None
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        stmt = (
            select(PlanRun.id)
            .where(
                PlanRun.parent_plan_run_id.is_(None),
                PlanRun.started_at >= since,
                PlanRun.run_context.contains({"schedule_id": int(schedule_id)}),
            )
            .limit(1)
        )
    else:
        marker = f'"schedule_id": {int(schedule_id)}'
        stmt = (
            select(PlanRun.id)
            .where(
                PlanRun.parent_plan_run_id.is_(None),
                PlanRun.started_at >= since,
                text("CAST(run_context AS TEXT) LIKE :pat").bindparams(pat=f"%{marker}%"),
            )
            .limit(1)
        )

    row = (await db.execute(stmt)).first()
    return row is not None


async def _fire_schedule(db, sched: "TaskSchedule", now: datetime) -> None:
    """Evaluate and fire a single TaskSchedule row (ADR-0020: Plan-based)."""
    schedule_id = sched.id
    plan_id = sched.plan_id
    cron_expression = sched.cron_expression
    schedule_name = sched.name
    device_ids = list(sched.device_ids or [])

    if plan_id:
        from backend.models.plan_run import PlanRun

        # ── 1. schedule 抖动去重（ADR-0020 §"落地 9"）──
        dedup_since = now - timedelta(seconds=_sched().schedule_dedup_window_seconds)
        try:
            if await _recently_triggered_by_schedule(db, schedule_id, dedup_since):
                logger.info(
                    "cron_skip_dedup schedule_id=%s plan_id=%s window=%.1fs",
                    schedule_id, plan_id, _sched().schedule_dedup_window_seconds,
                )
                sched.next_run_at = _next_schedule_run(cron_expression, now)
                return
        except Exception:
            await db.rollback()
            logger.warning(
                "cron_dedup_check_failed schedule_id=%s — failing open",
                schedule_id, exc_info=True,
            )

        # ── 2. plan 严格防重叠（#994 裁决：不允许排队） ──
        # 同一 Plan 存在任一非终态 Run（QUEUED / PRECHECK / RUNNING）→ 跳过本
        # 窗口并推进 next_run_at：排队态不积压、长跑不豁免（不设 started_at 年龄
        # 阈值）、错过不补跑。卡住的非终态行由 recycler / precheck_reaper 收口，
        # 需要补跑请用 CHAIN / 手动触发。
        try:
            active_result = await db.execute(
                select(PlanRun.id)
                .where(
                    PlanRun.plan_id == plan_id,
                    PlanRun.status.in_(
                        (
                            PlanRunStatus.QUEUED.value,
                            PlanRunStatus.PRECHECK.value,
                            PlanRunStatus.RUNNING.value,
                        )
                    ),
                )
                .limit(1)
            )
            if active_result.scalars().all():
                logger.info(
                    "cron_skip_overlap schedule_id=%s plan_id=%s — "
                    "同 Plan 存在非终态 Run（严格防重叠，不排队）",
                    schedule_id, plan_id,
                )
                sched.next_run_at = _next_schedule_run(cron_expression, now)
                return
        except Exception as e:
            await db.rollback()
            logger.warning(
                "overlap_check_failed schedule_id=%s, skipping dispatch (fail-closed): %s",
                schedule_id, e,
            )
            sched.next_run_at = _next_schedule_run(cron_expression, now)
            return

        await _dispatch_plan_async(
            plan_id, device_ids, db, schedule_id=schedule_id,
        )
        logger.info(
            "cron_plan_dispatched schedule_id=%s plan_id=%s",
            schedule_id, plan_id,
        )
    else:
        logger.error(
            "cron_schedule_skip_no_plan schedule_id=%s name=%s — "
            "plan_id is required",
            schedule_id, schedule_name,
        )

    sched.last_run_at = schedule_timestamp(now)
    sched.next_run_at = _next_schedule_run(cron_expression, now)
    logger.info(
        "cron_schedule_updated schedule_id=%s next_run=%s",
        schedule_id, sched.next_run_at,
    )


async def check_and_fire_schedules() -> None:
    """One tick of the cron schedule checker.

    Queries ``TaskSchedule`` rows whose ``next_run_at`` has passed and fires
    each eligible schedule.  Called by APScheduler ``IntervalTrigger``.
    """
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        # next_run_at is TIMESTAMP WITHOUT TIME ZONE; strip tz for comparison
        now_naive = schedule_timestamp(now)
        result = await db.execute(
            select(TaskSchedule).where(
                TaskSchedule.enabled == True,  # noqa: E712
                TaskSchedule.next_run_at <= now_naive,
            )
        )
        schedules = result.scalars().all()

        for sched in schedules:
            try:
                await _fire_schedule(db, sched, now)
            except Exception:
                logger.exception("cron_schedule_execute_error schedule_id=%s", sched.id)
                await db.rollback()

        if schedules:
            await db.commit()
            logger.info("cron_scheduler_fired count=%d", len(schedules))




def purge_run_storage_dirs(run_ids: list) -> set:
    """#1521/#1698: 删除 PlanRun 的 NFS 目录（devices/dedup/jira）。

    DB 行是「哪些目录属于此 run」的唯一索引——必须在删行**之前**清理，
    否则行删后目录永不可回溯（R-01 盘满链：DB 轨有 TTL、NFS 轨无 TTL）。
    返回删除失败的 run_id 集合（调用方应从本批 DB 删除中剔除，下轮重试
    文件清理——先文件后行的顺序保证失败可自愈）。
    """
    from backend.core.storage_root import resolve_shared_storage_root

    root = resolve_shared_storage_root()
    if not root:
        logger.warning("nfs_retention_skipped_root_unset")
        return set()

    base = Path(root)
    failed: set = set()
    removed = 0
    for run_id in run_ids:
        # jira/{run_id}/ holds extract bundles (#1698); omit → orphan after row delete.
        for sub in ("devices", "dedup", "jira"):
            target = base / sub / str(int(run_id))
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                    removed += 1
            except Exception:
                failed.add(run_id)
                logger.warning(
                    "nfs_retention_purge_failed dir=%s", target, exc_info=True,
                )
    if removed:
        logger.info("nfs_retention_purged dirs=%d failed_runs=%d", removed, len(failed))
    return failed


def _retention_candidate_ids(db, cutoff: datetime, limit: int = 100) -> list[int]:
    """Bounded leaf-first batch; references are filtered before each LIMIT.

    #2022：本函数**只读**，不再 `FOR UPDATE`。加锁顺序由
    :func:`_retention_prelock_subtree`（job → lease）与 :func:`_retention_lock_runs`
    （plan_run）承担——原先在此处先锁 plan_run，与热路径的
    `job → lease → plan_run` 相反，见 `_retention_prelock_subtree` 的说明。
    #2105：``limit`` 由 ``plan_run_retention_batch_size`` 提供（持锁窗口的杠杆）。
    """
    from sqlalchemy.orm import aliased

    from backend.models.plan_run import PlanRun

    dependent = aliased(PlanRun)
    selected_ids: list[int] = []
    while len(selected_ids) < limit:
        referenced = db.query(dependent.id).filter(
            dependent.id != PlanRun.id,
            dependent.id.notin_(selected_ids),
            or_(
                dependent.parent_plan_run_id == PlanRun.id,
                dependent.root_plan_run_id == PlanRun.id,
            ),
        ).exists()
        frontier = (
            db.query(PlanRun.id)
            .filter(
                PlanRun.status.in_(["SUCCESS", "FAILED", "PARTIAL_SUCCESS"]),
                PlanRun.started_at < cutoff,
                PlanRun.id.notin_(selected_ids),
                ~referenced,
            )
            .order_by(PlanRun.started_at, PlanRun.id)
            .limit(limit - len(selected_ids))
            .all()
        )
        if not frontier:
            break
        selected_ids.extend(run_id for (run_id,) in frontier)
    return selected_ids


def _retention_prelock_subtree(db, run_ids: list[int]) -> None:
    """#2022：按共享行加锁全序**预锁**候选 run 的 job / lease 行（job → lease）。

    必须在锁 `plan_run` 之前调用。本函数（保留清理）原先在
    :func:`_retention_candidate_ids` 里先 `FOR UPDATE` `plan_run`，随后才 DELETE
    `device_leases` / `job_instance` —— 与 complete / recycler / reconciler /
    coordinator-heartbeat 的 `job → lease → plan_run` **相反**，争用同一行即成环
    （与 `#1959` / `#1980` / `#1985` 同源的死锁家族；全序表见
    `docs/notes/architecture/2026-09-14-shared-row-lock-table.md`）。

    这里只把本函数稍后**本来就会删**的同一批行提前按 id 升序锁住——下方删除内容、
    删除顺序、候选过滤与判定一律不变，因此不改变 retention 的语义。

    候选 run 全部是「已终态且超过保留期」，其 job/lease 行基本无并发争用；预锁的
    代价是锁面提前放大（命中率低），换来的是与热路径同序、不成环。
    """
    from backend.models.device_lease import DeviceLease
    from backend.models.job import JobInstance

    if not run_ids:
        return
    job_ids = [
        row[0]
        for row in db.execute(
            select(JobInstance.id)
            .where(JobInstance.plan_run_id.in_(run_ids))
            .order_by(JobInstance.id)
            .with_for_update()
        ).all()
    ]
    if not job_ids:
        return
    # I1（job → device_leases）：lease 行同样要在 plan_run 之前锁住。
    db.execute(
        select(DeviceLease.id)
        .where(DeviceLease.job_id.in_(job_ids))
        .order_by(DeviceLease.id)
        .with_for_update()
    ).all()


def _retention_lock_runs(db, run_ids: list[int], cutoff: datetime) -> list[int]:
    """#2022：锁内**复核**候选仍满足「终态 + 超过保留期」，并按 id 升序锁住。

    候选来自无锁预读，可能已被并发改动（状态变化、被其它 worker 锁住），故必须
    在锁内重验；`SKIP LOCKED` 保留原先由候选查询承担的互斥语义（两个 worker 不会
    重复删除同一 run）。未通过复核的不进入本批（下轮重试），安全。

    链式引用（parent/root）不在本函数复核：那由随后的 `_retention_safe_ids` 在
    **锁内**用当前库状态计算闭包，本函数只负责「锁 + 终态/年龄复核」。
    """
    from backend.models.plan_run import PlanRun

    if not run_ids:
        return []
    rows = db.execute(
        select(PlanRun.id)
        .where(
            PlanRun.id.in_(run_ids),
            PlanRun.status.in_(["SUCCESS", "FAILED", "PARTIAL_SUCCESS"]),
            PlanRun.started_at < cutoff,
        )
        .order_by(PlanRun.id)
        .with_for_update(skip_locked=True)
    ).all()
    return [run_id for (run_id,) in rows]


def _retention_safe_ids(db, run_ids: list[int]) -> tuple[list[int], set[int]]:
    """Preserve outside references and their ancestors, including deferred purges."""
    from backend.models.plan_run import PlanRun

    run_id_set = set(run_ids)
    if not run_id_set:
        return [], set()
    ref_rows = (
        db.query(PlanRun.parent_plan_run_id, PlanRun.root_plan_run_id)
        .filter(
            or_(
                PlanRun.parent_plan_run_id.in_(run_id_set),
                PlanRun.root_plan_run_id.in_(run_id_set),
            ),
            ~PlanRun.id.in_(run_id_set),
        )
        .all()
    )
    keep = {reference for refs in ref_rows for reference in refs if reference in run_id_set}
    if keep:
        chain = (
            db.query(PlanRun.id, PlanRun.parent_plan_run_id, PlanRun.root_plan_run_id)
            .filter(PlanRun.id.in_(run_id_set))
            .all()
        )
        ancestors = {run_id: (parent_id, root_id) for run_id, parent_id, root_id in chain}
        pending = list(keep)
        while pending:
            for ancestor in ancestors.get(pending.pop(), (None, None)):
                if ancestor in run_id_set and ancestor not in keep:
                    keep.add(ancestor)
                    pending.append(ancestor)
    return sorted(run_id_set - keep), keep


def run_retention_cleanup() -> None:
    """Delete completed PlanRuns older than _sched().plan_run_retention_days (ADR-0020).

    Runs as an independent APScheduler job (sync, runs in thread-pool).
    """
    from backend.models.plan_run import PlanRun
    from backend.models.job import JobArtifact, JobInstance, JobLogSignal, StepTrace
    from backend.models.device_lease import DeviceLease
    from backend.models.device_log_event import DeviceLogEvent
    from backend.models.resource_pool import ResourceAllocation

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_sched().plan_run_retention_days)

    # 持锁窗口（#2104）：候选读之前为 None——失败路径可能在取锁前就抛，
    # 上报点须先判非 None，否则 except 分支自己会 NameError。
    lock_t0: float | None = None
    with SessionLocal() as db:
        try:
            # 批大小是**持锁窗口的杠杆**（#2105）：窗口 ∝ 本 tick 处理的 run 数。
            batch_size = _sched().plan_run_retention_batch_size
            run_ids = _retention_candidate_ids(db, cutoff, limit=batch_size)
            # #2144：候选数与批大小成对上报，让「清理跟不上」可判定（candidates >=
            # batch_size 即本轮批被填满 = 队列里还有到期 run）。刻意放在两个早退**之前**：
            # 清空后 gauge 必须回到 0，而不是停在上一轮的非零值上。
            record_retention_candidates(len(run_ids), batch_size)
            if not run_ids:
                return

            # #2104：从这里起持有行锁，直到事务结束/会话关闭——窗口长度会上报到
            # stability_retention_txn_seconds。锁序统一（#2022）之后，「等待」取代
            # 「死锁」成为这一批行的代价，而窗口长度就是这个代价的上界。
            lock_t0 = time.perf_counter()

            # #2022：按共享行加锁全序取锁——job → lease（预锁子树）→ plan_run。
            # 顺序不可调换：反过来（先 plan_run）会与 complete / recycler /
            # reconciler 的 job → plan_run 成环（详见 _retention_prelock_subtree）。
            _retention_prelock_subtree(db, run_ids)
            run_ids = _retention_lock_runs(db, run_ids, cutoff)
            if not run_ids:
                return

            safe_run_ids, keep = _retention_safe_ids(db, run_ids)
            if not safe_run_ids:
                logger.info(
                    "retention_cleanup skipped: all %d candidates chain-referenced",
                    len(run_ids),
                )
                return

            # Subquery: job IDs belonging to safely-deletable PlanRuns
            # #1521/#1698: NFS 轨回收——DB 行删除前先清 devices/dedup/jira
            # （行是目录的唯一索引）；文件删除失败的 run 剔除出本批 DB 删除，
            # 下轮重试（先文件后行，失败可自愈）。
            purge_failed = purge_run_storage_dirs(safe_run_ids)
            if purge_failed:
                safe_run_ids, deferred_ancestors = _retention_safe_ids(
                    db, [run_id for run_id in safe_run_ids if run_id not in purge_failed],
                )
                keep.update(deferred_ancestors)
                if not safe_run_ids:
                    logger.warning(
                        "retention_cleanup deferred: NFS failures=%d kept_ancestors=%d",
                        len(purge_failed), len(deferred_ancestors),
                    )
                    # 整批被 NFS 失败推迟：锁要到会话关闭才释放，窗口记在返回前。
                    record_retention_txn(time.perf_counter() - lock_t0)
                    return

            stale_job_ids = select(JobInstance.id).where(
                JobInstance.plan_run_id.in_(safe_run_ids)
            )
            # #798: 删行前收集 job 清单——DB 事务提交后据此清理 console.log
            # 文件与内存锁（否则系统盘与 _locks 随历史运行无界增长）。
            stale_job_id_list = [
                row[0] for row in db.execute(stale_job_ids).all()
            ]

            # FK order: child tables first
            db.query(StepTrace).filter(
                StepTrace.job_id.in_(stale_job_ids)
            ).delete(synchronize_session=False)
            db.query(DeviceLease).filter(
                DeviceLease.job_id.in_(stale_job_ids)
            ).delete(synchronize_session=False)
            db.query(ResourceAllocation).filter(
                ResourceAllocation.job_instance_id.in_(stale_job_ids)
            ).delete(synchronize_session=False)
            db.query(JobArtifact).filter(
                JobArtifact.job_id.in_(stale_job_ids)
            ).delete(synchronize_session=False)

            # #781: job_log_signal.job_id / device_log_event.{job,plan_run}_id
            # 均为 ON DELETE SET NULL（非 CASCADE）。删 Job/PlanRun 前必须显式
            # 删行，否则 signal/event 变孤儿并单调堆积（仅 /log-signals/orphans
            # 可见，且是 #729 幽灵 /complete 404 的跨保留窗口来源之一）。
            # 先 signal 再 event：signal.device_log_event_id 亦为 SET NULL。
            db.query(JobLogSignal).filter(
                JobLogSignal.job_id.in_(stale_job_ids)
            ).delete(synchronize_session=False)
            db.query(DeviceLogEvent).filter(
                or_(
                    DeviceLogEvent.plan_run_id.in_(safe_run_ids),
                    DeviceLogEvent.job_id.in_(stale_job_ids),
                )
            ).delete(synchronize_session=False)

            db.query(JobInstance).filter(
                JobInstance.plan_run_id.in_(safe_run_ids)
            ).delete(synchronize_session=False)
            db.query(PlanRun).filter(
                PlanRun.id.in_(safe_run_ids)
            ).delete(synchronize_session=False)
            db.commit()
            # 窗口到此为止（提交即释放行锁）；**不要**把它挪到下面的 console log
            # 清理之后——那是提交后的文件操作，不属于持锁窗口。
            record_retention_txn(time.perf_counter() - lock_t0)
            logger.info(
                "retention_cleanup deleted runs=%d kept_chain_referenced=%d",
                len(safe_run_ids),
                len(keep),
            )
            # #798: DB 行删除事务已提交——best-effort 清理日志文件与锁。
            try:
                from backend.realtime.log_writer import purge_job_log_files

                purged = purge_job_log_files(stale_job_id_list)
                if purged:
                    logger.info(
                        "retention_cleanup purged_console_logs jobs=%d", purged,
                    )
            except Exception:
                logger.warning("retention_console_purge_failed", exc_info=True)
        except Exception:
            logger.warning("retention_cleanup failed", exc_info=True)
            db.rollback()
            # 失败路径同样占着行锁（回滚前），一并计入窗口；取锁前就失败则不上报。
            if lock_t0 is not None:
                record_retention_txn(time.perf_counter() - lock_t0)


def _terminal_archive_complete(db, plan_run_id: int) -> bool:
    """True when merge artifact exists and extract stage was recorded (#1110)."""
    from backend.models.plan_run import PlanRun
    from backend.models.plan_run_artifact import PlanRunArtifact

    merge_count = db.execute(
        select(func.count()).select_from(PlanRunArtifact).where(
            PlanRunArtifact.plan_run_id == plan_run_id,
            PlanRunArtifact.artifact_type == "merge_result_xls",
        )
    ).scalar_one()
    if merge_count == 0:
        return False
    run = db.get(PlanRun, plan_run_id)
    if run is None:
        return False
    extract = (run.run_context or {}).get("extract")
    return isinstance(extract, dict)


def auto_archive_sweep() -> None:
    """Enqueue scan→upload→merge for at most one PlanRun per Plan.

    ADR-0025 Sprint 4 five-trigger scenario 5: after a PlanRun reaches terminal
    status (or while one is still RUNNING on long patrol), the configured
    ``Plan.auto_archive_interval_seconds`` elapses and the scheduler triggers
    dedup incrementally.

    Selection (one run per plan per sweep):
      1. If the Plan has a RUNNING PlanRun → that is the active run.
      2. Else → the oldest due terminal PlanRun that still needs archive
         (``ended_at + interval`` elapsed and not archive-complete). Newest-first
         selection starved earlier runs when a later run existed (#833).

    Terminal runs: final scan (``is_final=True``) after ``ended_at + interval``;
    skip only when merge artifact + extract context are present (#1110).

    RUNNING runs: incremental ``is_final=False`` scans while patrol is active, rate-limited
    by ``auto_archive_interval_seconds`` since the last scan artifact.
    """
    from backend.models.enums import PlanRunStatus
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun
    from backend.models.plan_run_artifact import PlanRunArtifact
    from backend.services.dedup_scan import enqueue_dedup_terminal_sync

    _AUTO_FINAL_STATUSES = {
        PlanRunStatus.SUCCESS.value,
        PlanRunStatus.PARTIAL_SUCCESS.value,
    }
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        try:
            plans = (
                db.query(Plan)
                .filter(Plan.auto_archive_interval_seconds.isnot(None))
                .all()
            )

            triggered = 0
            for plan in plans:
                interval = plan.auto_archive_interval_seconds
                running = (
                    db.query(PlanRun)
                    .filter(
                        PlanRun.plan_id == plan.id,
                        PlanRun.status == PlanRunStatus.RUNNING.value,
                    )
                    .order_by(PlanRun.id.desc())
                    .first()
                )
                if running is not None:
                    scan_count = db.execute(
                        select(func.count()).select_from(PlanRunArtifact).where(
                            PlanRunArtifact.plan_run_id == running.id,
                            PlanRunArtifact.artifact_type == "scan_result_xls",
                        )
                    ).scalar_one()
                    if scan_count > 0:
                        last_scan_at = db.execute(
                            select(func.max(PlanRunArtifact.created_at)).where(
                                PlanRunArtifact.plan_run_id == running.id,
                                PlanRunArtifact.artifact_type == "scan_result_xls",
                            )
                        ).scalar_one()
                        if last_scan_at and now - last_scan_at < timedelta(seconds=interval):
                            continue
                    enqueue_dedup_terminal_sync(running.id, is_final=False)
                    triggered += 1
                    continue

                # No RUNNING: serve the oldest due terminal run still needing archive (#833).
                due_cutoff = now - timedelta(seconds=interval)
                candidates = (
                    db.query(PlanRun)
                    .filter(
                        PlanRun.plan_id == plan.id,
                        PlanRun.status.in_(_AUTO_FINAL_STATUSES),
                        PlanRun.ended_at.isnot(None),
                        PlanRun.ended_at <= due_cutoff,
                    )
                    .order_by(PlanRun.ended_at.asc(), PlanRun.id.asc())
                    .all()
                )
                for run in candidates:
                    scan_count = db.execute(
                        select(func.count()).select_from(PlanRunArtifact).where(
                            PlanRunArtifact.plan_run_id == run.id,
                            PlanRunArtifact.artifact_type == "scan_result_xls",
                        )
                    ).scalar_one()
                    if scan_count > 0 and _terminal_archive_complete(db, run.id):
                        continue
                    enqueue_dedup_terminal_sync(run.id, is_final=True)
                    triggered += 1
                    break

            if triggered:
                logger.info("auto_archive_sweep triggered=%d", triggered)
        except Exception:
            logger.warning("auto_archive_sweep failed", exc_info=True)
            db.rollback()
