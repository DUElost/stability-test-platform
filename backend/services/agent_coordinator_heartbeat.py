"""Agent Coordinator 心跳（#1520 垂直切片：agent_api coordinator-heartbeat）。

``POST /coordinator-heartbeat``：agent_instance fencing → job 先于 PlanRunHost
写锁序（#1980）→ epoch fencing → phase 同步。

路由退化为 ``ok(await record_agent_coordinator_heartbeat(...))``。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.enums import JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRunHost
from backend.services.agent_lease_extend import (
    _VALID_EXECUTION_STATES,
    _parse_progress_ts,
)

logger = logging.getLogger(__name__)


class _CoordinatorHeartbeatJob(BaseModel):
    job_id: int
    execution_state: Optional[str] = None
    last_progress_at: Optional[str] = None  # ISO8601


class _CoordinatorHeartbeatIn(BaseModel):
    host_id: str
    agent_instance_id: str
    # coordinator_epoch is now inside each plan_run_hosts entry (Step 5b收口 #10).
    # A single top-level epoch would incorrectly share one epoch across hosts.
    plan_run_hosts: List[dict]  # [{id, plan_run_id, host_id, coordinator_epoch, phase}]
    jobs: List[_CoordinatorHeartbeatJob] = []


class _CoordinatorHeartbeatOut(BaseModel):
    accepted: bool
    stale_plan_run_host_ids: List[int] = []  # epoch was already higher → Agent must reconcile
    agent_instance_stale: bool = False  # host already bound to a newer agent instance
    current_coordinator_epochs: Dict[int, int] = {}  # prh_id → control-plane epoch


_VALID_COORDINATOR_PHASES = {
    "INIT",
    "BARRIER_WAIT",
    "PATROL",
    "TEARDOWN",
}


def _prh_lock_key(entry: dict) -> int:
    """#2796：plan_run_host 行锁的全序键；非整型 id 归 0（随后被 `if not prh_id` 跳过）。"""
    try:
        return int(entry.get("id") or 0)
    except (TypeError, ValueError):
        return 0


async def record_agent_coordinator_heartbeat(
    db: AsyncSession,
    payload: _CoordinatorHeartbeatIn,
) -> _CoordinatorHeartbeatOut:
    """ADR-0026 Step 5b: per-host Coordinator liveness + per-job state sync.

    - Rejects heartbeats from a superseded ``agent_instance_id`` (host already
      bound to a newer Agent process) so the old Coordinator self-fences.
    - Bumps coordinator_heartbeat_at for every PlanRunHost in the payload
      where coordinator_epoch >= stored epoch (epoch fencing).
    - Persists execution_state + last_progress_at for every listed job
      (RUNNING-guarded).
    - Returns which PlanRunHost rows were stale (higher epoch already seen)
      so the Agent can reconcile (terminate that Coordinator instance).
    """
    now = datetime.now(timezone.utc)
    stale_host_ids: list[int] = []
    current_epochs: dict[int, int] = {}

    # Agent-instance fencing: a restarted Agent claims a new instance id via
    # host heartbeat; an old Coordinator process must not rewrite projections.
    host = await db.get(Host, payload.host_id)
    if host is not None:
        stored_instance = (host.last_agent_instance_id or "").strip()
        reported_instance = (payload.agent_instance_id or "").strip()
        if stored_instance and reported_instance and stored_instance != reported_instance:
            for entry in payload.plan_run_hosts:
                prh_id = entry.get("id")
                if not prh_id:
                    continue
                row = await db.get(PlanRunHost, prh_id)
                if row is None:
                    continue
                stale_host_ids.append(int(prh_id))
                current_epochs[int(prh_id)] = int(row.coordinator_epoch or 0)
            logger.warning(
                "coord_hb_agent_instance_stale host=%s stored=%s reported=%s prh=%s",
                payload.host_id, stored_instance, reported_instance, stale_host_ids,
            )
            return _CoordinatorHeartbeatOut(
                accepted=False,
                agent_instance_stale=True,
                stale_plan_run_host_ids=stale_host_ids,
                current_coordinator_epochs=current_epochs,
            )

    # #1980：先写 job_instance，再写 plan_run_host —— 与终态化路径保持同一全序。
    # complete_job / 回收器先持 job 行锁，再由 on_job_terminal → _bump_host_counters
    # 更新 plan_run_host（job_instance → plan_run_host）。本端点原先相反：先改
    # PlanRunHost（ORM 变更在后续语句的 autoflush 里落库），再 UPDATE job_instance，
    # 于是与终态化形成环路等待（`coordinator_heartbeat` 持 prh 行等 job 行，终态化
    # 持 job 行等 prh 行）。两个循环互相独立，交换顺序即可；下面的
    # `db.execute(update(JobInstance))` 会先执行并锁住 job 行，plan_run_host 的变更
    # 随后才 flush。
    #
    # #2796：集合**内部**同样要全序——payload 序来自 agent 侧字典插入序，与
    # extend_leases_batch 的 `ORDER BY id`（#992 全序约定）交错仍可成环，故按
    # job_id 升序取锁。
    for j in sorted(payload.jobs, key=lambda item: item.job_id):
        reported = j.execution_state
        state_val = reported if reported in _VALID_EXECUTION_STATES else None
        # ADR-0026 §3 clock discipline (#288):
        # - WAITING_*/PATROL_SLEEP: refresh waiting clock (and never EXECUTING).
        # - EXECUTING_STEP: persist state only — execution hb comes from
        #   extend-batch.
        # - Unknown/null state: nothing to persist.
        # - Whenever we do write, updated_at is pinned (never bumped): the
        #   recycler judges liveness solely by the execution signals.
        values: dict[str, Any] = {}
        if state_val is not None:
            values["execution_state"] = state_val
        if state_val in ("WAITING_EXECUTION_SLOT", "PATROL_SLEEP", "WAITING_BARRIER"):
            values["last_execution_heartbeat_at"] = now
        if not values:
            continue
        values["updated_at"] = JobInstance.updated_at
        ts = _parse_progress_ts(j.last_progress_at)
        if ts is not None:
            values["last_progress_at"] = ts
        await db.execute(
            update(JobInstance)
            .where(
                JobInstance.id == j.job_id,
                JobInstance.host_id == payload.host_id,
                JobInstance.status == JobStatus.RUNNING.value,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    # #2796：plan_run_host 集合内部同样按 id 升序取锁（同上；#1980 的方向序不变）。
    for entry in sorted(payload.plan_run_hosts, key=_prh_lock_key):
        prh_id = entry.get("id")
        pr_id = entry.get("plan_run_id")
        hid = entry.get("host_id")
        reported_epoch = entry.get("coordinator_epoch", 0)
        if not prh_id or not pr_id or not hid:
            continue
        row = await db.get(PlanRunHost, prh_id)
        if row is None:
            continue
        # Ownership validation: the PlanRunHost row must belong to THIS host
        # AND the requesting agent_instance (Step 5b收口).
        if row.host_id != payload.host_id:
            logger.warning(
                "coord_hb_host_mismatch prh=%d claimed=%s actual=%s",
                prh_id, payload.host_id, row.host_id,
            )
            stale_host_ids.append(prh_id)
            current_epochs[int(prh_id)] = int(row.coordinator_epoch or 0)
            continue
        if row.coordinator_epoch > reported_epoch:
            stale_host_ids.append(prh_id)
            current_epochs[int(prh_id)] = int(row.coordinator_epoch or 0)
            continue
        row.coordinator_epoch = max(row.coordinator_epoch, reported_epoch)
        row.coordinator_heartbeat_at = now
        reported_phase = entry.get("phase")
        if reported_phase in _VALID_COORDINATOR_PHASES:
            row.phase = reported_phase

    await db.commit()
    return _CoordinatorHeartbeatOut(
        accepted=len(stale_host_ids) == 0,
        stale_plan_run_host_ids=stale_host_ids,
        current_coordinator_epochs=current_epochs,
    )




# 路由 / 既有测试用的端点别名。
coordinator_heartbeat = record_agent_coordinator_heartbeat
