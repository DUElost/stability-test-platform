"""Claim / poll tick extracted from ``main`` (#736).

One iteration of the Agent claim loop: capacity → fetch → occupy → register →
submit. ``main`` keeps the ``while`` / signal / ``finally`` shutdown shell.
"""

from __future__ import annotations

import logging
from concurrent.futures import Executor
from typing import Any, Callable

from .active_job_bindings import ActiveJobOccupancy
from .api_client import fetch_pending_jobs
from .job_runner import _arrive_patrol_barrier_preengine
from .recovery_executor import _make_local_worker_token, _rollback_failed_claim

logger = logging.getLogger(__name__)


def process_claim_tick(
    *,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    occupancy: ActiveJobOccupancy,
    heartbeat_thread: Any,
    register_active_job: Callable[..., None],
    deregister_active_job: Callable[..., None],
    lease_renewer: Any,
    local_db: Any,
    coordinator: Any,
    executor: Executor,
    adb: Any,
    job_runner_state: Any,
    mq_producer: Any,
    script_registry: Any,
    patrol_checkpoint_store: Any,
    operation_scheduler: Any,
    step_trace_uploader: Any,
    run_task_wrapper: Callable[..., Any],
) -> None:
    """ADR-0026 Step 5b claim tick (free healthy device capacity)."""
    with occupancy.lock:
        active_count = len(occupancy.job_ids)

    # ADR-0026 Step 5b: claim capacity = free healthy devices only.
    # MAX_CONCURRENT_TASKS no longer restricts concurrent RUNNING
    # jobs — the OperationScheduler independently limits script
    # execution concurrency. All admitted devices can be RUNNING.
    available_slots = heartbeat_thread.effective_slots

    logger.info("main_loop_tick active=%d slots=%d", active_count, available_slots)

    if available_slots <= 0:
        return

    jobs = fetch_pending_jobs(
        api_url, host_id, agent_instance_id, capacity=available_slots
    )
    jobs = jobs[:available_slots]

    if jobs:
        logger.info(
            "pending_jobs_fetched host_id=%s count=%d slots=%d job_ids=%s",
            host_id,
            len(jobs),
            available_slots,
            [job.get("id") for job in jobs],
        )
    else:
        logger.debug(
            "no_pending_jobs host_id=%s active=%d slots=%d",
            host_id,
            active_count,
            available_slots,
        )

    for claimed_job in jobs:
        job = dict(claimed_job)
        device_id = job.get("device_id")

        with occupancy.lock:
            if device_id and device_id in occupancy.device_ids:
                logger.debug(
                    "skip_device_busy job=%d device=%d",
                    job["id"],
                    device_id,
                )
                continue
            if device_id:
                occupancy.device_ids.add(device_id)
                occupancy.device_owner[device_id] = job["id"]

        local_worker_token = _make_local_worker_token(
            job["id"],
            job["fencing_token"],
        )
        job["local_worker_token"] = local_worker_token
        job["agent_instance_id"] = agent_instance_id

        # ADR-0019 Phase 2b + 3a: 注册 job + fencing_token + 持久化 active_job
        # R07-F13 (#1013): 本地（SQLite）登记失败必须补偿——否则设备忙占位
        # 残留且已认领未登记任务无显式归宿。回滚占位/令牌后跳批继续，让
        # server 下次 tick 或 recovery 重新对账该 claim。
        try:
            register_active_job(
                job["id"],
                job["fencing_token"],
                device_id,
                job.get("device_serial", ""),
                local_worker_token,
            )
        except Exception:
            logger.exception(
                "register_active_job_failed job=%d device=%s — rolling back claim",
                job["id"],
                device_id,
            )
            _rollback_failed_claim(
                jid=job["id"],
                fencing_token=job.get("fencing_token", ""),
                local_worker_token=local_worker_token,
                device_id=device_id,
                active_jobs_lock=occupancy.lock,
                active_job_ids=occupancy.job_ids,
                active_device_ids=occupancy.device_ids,
                active_job_tokens=occupancy.job_tokens,
                active_device_owner=occupancy.device_owner,
                lease_renewer=lease_renewer,
                local_db=local_db,
            )
            continue

        # ADR-0026 Step 5b: register job + PlanRunHost with coordinator
        coordinator.register_job(job["id"])
        if device_id:
            coordinator.register_job_device(job["id"], device_id)
        prh_id = job.get("plan_run_host_id")
        plan_run_id = job.get("plan_run_id")
        if prh_id and plan_run_id:
            coordinator.register_plan_run_host(prh_id, plan_run_id)
        try:
            executor.submit(
                run_task_wrapper,
                job,
                adb,
                api_url,
                host_id,
                job_runner_state,
                mq_producer,
                script_registry,
                local_db,
                patrol_checkpoint_store,
                operation_scheduler=operation_scheduler,
                coordinator=coordinator,
                step_trace_uploader=step_trace_uploader,  # #483
            )
        except Exception:
            logger.exception(
                "submit_failed job=%d device=%s", job["id"], device_id
            )
            # #801: submit 失败的作业不会进引擎——补记 barrier
            # 到达，避免同 wave peer 空等 barrier_timeout。
            _arrive_patrol_barrier_preengine(
                job,
                coordinator,
                job["id"],
            )
            deregister_active_job(
                job["id"],
                job.get("fencing_token", ""),
                local_worker_token,
            )
            with occupancy.lock:
                if device_id:
                    occupancy.device_ids.discard(device_id)
