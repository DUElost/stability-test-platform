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




def _within_shared_root(target: Path, resolved_base: Path) -> bool:
    """#2031：容器化校验——解析符号链接后必须仍落在共享根内。

    共享根下若存在指向根外的符号链接目录，``is_dir()`` 会跟随、``rmtree``
    会删到根外（与 #1825 收口的威胁模型同类，需共享根写权限才可利用）。
    越界一律不删：计入 failed 并告警（宁可推迟 DB 行删除，也不误删根外数据）。
    """
    try:
        resolved = target.resolve()
    except OSError:
        return False
    return resolved == resolved_base or resolved.is_relative_to(resolved_base)


def purge_run_storage_dirs(run_ids: list, jobs_by_run: dict | None = None) -> set:
    """#1521/#1698/#2031: 删除 PlanRun 在共享存储上的目录。

    DB 行是「哪些目录属于此 run」的唯一索引——必须在删行**之前**清理，
    否则行删后目录永不可回溯（R-01 盘满链：DB 轨有 TTL、NFS 轨无 TTL）。
    返回删除失败的 run_id 集合（调用方应从本批 DB 删除中剔除，下轮重试
    文件清理——先文件后行的顺序保证失败可自愈）。

    覆盖两类桶（同根、不同分桶维度）：

    - ``devices|dedup|jira/{run_id}/`` —— 按 **run** 分桶，由 ``run_ids`` 展开；
    - ``jobs/{job_id}/``（#2031）—— 按 **job** 分桶（``backend/agent/aee/paths.py``
      的 artifact promote 与 Watcher LogPuller 落点），由 ``jobs_by_run``
      （``{run_id: [job_id, ...]}``）展开。job 目录失败按**所属 run** 归因，
      与 run 目录失败同语义（该 run 整批推迟，行与文件都留在原地等下轮）。
    """
    from backend.core.storage_root import resolve_shared_storage_root

    root = resolve_shared_storage_root()
    if not root:
        logger.warning("nfs_retention_skipped_root_unset")
        return set()

    base = Path(root)
    resolved_base = base.resolve()
    failed: set = set()
    removed = 0
    jobs_by_run = jobs_by_run or {}

    def _purge(target: Path, run_id: int) -> None:
        nonlocal removed
        try:
            if not _within_shared_root(target, resolved_base):
                raise ValueError(f"path escapes shared storage root: {target}")
            if target.is_dir():
                shutil.rmtree(target)
                removed += 1
        except Exception:
            failed.add(run_id)
            logger.warning(
                "nfs_retention_purge_failed dir=%s", target, exc_info=True,
            )

    for run_id in run_ids:
        # jira/{run_id}/ holds extract bundles (#1698); omit → orphan after row delete.
        for sub in ("devices", "dedup", "jira"):
            _purge(base / sub / str(int(run_id)), run_id)
        # #2031：jobs/{job_id}/ 的唯一索引是 StepTrace/JobArtifact 行，而它们在
        # 同一批里被删（保留期 3 天 << artifact 清理器 30 天）——不在这里清掉即
        # 永不可回溯的孤儿目录，随 job 数线性累积。
        for job_id in jobs_by_run.get(run_id, ()):
            _purge(base / "jobs" / str(int(job_id)), run_id)
    if removed:
        logger.info("nfs_retention_purged dirs=%d failed_runs=%d", removed, len(failed))
    return failed


def _collect_unassigned_dirs(db, safe_run_ids: list, stale_job_ids, job_run_of: dict) -> dict:
    """#2262：本批将要删除的 DLE 行中，``remote_path`` 落在 ``devices/unassigned/``
    的目录 → 所属 run（失败归因用）。

    谓词与随后的 DLE 删除**逐字一致**（plan_run 或 job 命中）——只清「行确实要删」
    的那些目录；行仍在库中的目录不动（``remote_path`` 仍是 extract/回溯的依据）。
    """
    from backend.models.device_log_event import DeviceLogEvent

    rows = db.execute(
        select(
            DeviceLogEvent.remote_path,
            DeviceLogEvent.plan_run_id,
            DeviceLogEvent.job_id,
        ).where(
            or_(
                DeviceLogEvent.plan_run_id.in_(safe_run_ids),
                DeviceLogEvent.job_id.in_(stale_job_ids),
            ),
            DeviceLogEvent.remote_path.like("%/devices/unassigned/%"),
        )
    ).all()

    mapping: dict = {}
    for remote_path, plan_run_id, job_id in rows:
        if not remote_path:
            continue
        owner = plan_run_id if plan_run_id is not None else job_run_of.get(job_id)
        if owner is not None:
            mapping[str(remote_path)] = int(owner)
    return mapping


def purge_unassigned_event_dirs(paths_by_run: dict) -> set:
    """#2262：回收 ``devices/unassigned/{event_id}/`` 目录。

    DLE 行是它的唯一索引，而**关联不搬文件**（``associate_unassigned_events_to_plan_run``
    只填 ``plan_run_id``）——所以 run 级 purge 永远命中不到它，行删后目录即孤儿。
    本函数只在本批**行删除之前**清这些目录：增长被限制在保留期内，且不在行还活着时
    删文件（保住 ``remote_path`` 的可回溯性）。

    ``paths_by_run`` = ``{remote_path: run_id}``；失败按 run 归因（与 NFS 主轨同语义）。
    只接受 ``{root}/devices/unassigned/{event_id}`` 的**直接子目录**——越界或形态不符
    一律跳过并告警（fail-safe：宁可留孤儿，也不误删别处）。
    """
    from backend.core.storage_root import resolve_shared_storage_root

    if not paths_by_run:
        return set()
    root = resolve_shared_storage_root()
    if not root:
        logger.warning("nfs_retention_skipped_root_unset")
        return set()

    resolved_base = Path(root).resolve()
    unassigned_root = resolved_base / "devices" / "unassigned"
    failed: set = set()
    removed = 0
    for raw_path, run_id in paths_by_run.items():
        # remote_path 指向**事件目录内的一层**（``unassigned/{event_id}/{basename}``，
        # 见 event_uploader 的 dst 拼装，生产实测 7 段路径即此形态）；也容忍它直接
        # 就是事件目录。取到事件目录才能整桶删除。
        try:
            p = Path(str(raw_path)).resolve()
        except OSError:
            p = None
        if p is not None and p.parent.parent == unassigned_root:
            event_dir: Path | None = p.parent
        elif p is not None and p.parent == unassigned_root:
            event_dir = p
        else:
            event_dir = None
        if event_dir is None or not _within_shared_root(event_dir, resolved_base):
            # 形态不符/越界：**跳过并告警，不计入 failed**——那是数据问题（行留下、
            # 目录不删即可），而计入 failed 会让整个 run 的 retention 每轮推迟、
            # 永久停摆（初版就是按「event 目录 = remote_path」判的，被真实形态证伪）。
            logger.warning(
                "nfs_retention_unassigned_path_invalid dir=%s", raw_path,
            )
            continue
        try:
            if event_dir.is_dir():
                shutil.rmtree(event_dir)
                removed += 1
        except Exception:
            failed.add(run_id)
            logger.warning(
                "nfs_retention_unassigned_purge_failed dir=%s", event_dir, exc_info=True,
            )
    if removed:
        logger.info(
            "nfs_retention_unassigned_purged dirs=%d failed_runs=%d", removed, len(failed),
        )
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
                # #2278：取锁（含等待）**已经发生**，窗口必须上报——锁序统一后这类
                # 「纯等待后一无所获」的 tick 恰是 #2104 要观测的代价形态；早退不报
                # 等于把指标最该覆盖的那一类样本丢掉（剩下的三个上报点都在此处之后）。
                record_retention_txn(time.perf_counter() - lock_t0)
                return

            safe_run_ids, keep = _retention_safe_ids(db, run_ids)
            if not safe_run_ids:
                logger.info(
                    "retention_cleanup skipped: all %d candidates chain-referenced",
                    len(run_ids),
                )
                # #2278：同上——链引用全保留也是一次真实的持锁窗口。
                record_retention_txn(time.perf_counter() - lock_t0)
                return

            # #798/#2031: 删行前收集 job 清单（含 run 归属）——既供提交后的
            # console.log 清理，也供 NFS ``jobs/{job_id}/`` 回收（按 run 归因，
            # 该 run 的文件清理失败时整批推迟）。
            stale_job_rows = db.execute(
                select(JobInstance.id, JobInstance.plan_run_id).where(
                    JobInstance.plan_run_id.in_(safe_run_ids)
                )
            ).all()
            stale_job_id_list = [job_id for job_id, _run_id in stale_job_rows]
            jobs_by_run: dict = {}
            for job_id, run_id in stale_job_rows:
                jobs_by_run.setdefault(run_id, []).append(job_id)
            job_run_of = {
                job_id: run_id
                for run_id, job_ids in jobs_by_run.items()
                for job_id in job_ids
            }

            stale_job_ids = select(JobInstance.id).where(
                JobInstance.plan_run_id.in_(safe_run_ids)
            )

            # #2262：devices/unassigned/{event_id}/ 不随 run 分桶（关联只填 plan_run_id、
            # 不搬文件），run 级 purge 命中不到 → 与行删除同批清理，把增长限制在保留期内。
            # 单独一轮「先文件后行」：它的失败同样让所属 run 出批，且先于下面的 run/job
            # 目录回收——出批的 run 随后不会被清掉任何目录。
            unassigned_failed = purge_unassigned_event_dirs(
                _collect_unassigned_dirs(db, safe_run_ids, stale_job_ids, job_run_of)
            )
            if unassigned_failed:
                safe_run_ids, deferred_ancestors = _retention_safe_ids(
                    db, [run_id for run_id in safe_run_ids if run_id not in unassigned_failed],
                )
                keep.update(deferred_ancestors)
                if not safe_run_ids:
                    logger.warning(
                        "retention_cleanup deferred: unassigned NFS failures=%d "
                        "kept_ancestors=%d",
                        len(unassigned_failed), len(deferred_ancestors),
                    )
                    record_retention_txn(time.perf_counter() - lock_t0)
                    return

            # #1521/#1698/#2031: NFS 轨回收——DB 行删除前先清
            # devices|dedup|jira/{run_id} 与 jobs/{job_id}（行是目录的唯一索引）；
            # 文件删除失败的 run 剔除出本批 DB 删除，下轮重试（先文件后行，失败可自愈）。
            purge_failed = purge_run_storage_dirs(safe_run_ids, jobs_by_run)
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

            # 两轮延迟都可能收缩批次：定稿 job 清单（提交后据此清 console.log，
            # 出批 run 的 job 仍留在库里 → 不得清理）。
            surviving = set(safe_run_ids)
            stale_job_id_list = [
                job_id
                for run_id, job_ids in jobs_by_run.items()
                if run_id in surviving
                for job_id in job_ids
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
