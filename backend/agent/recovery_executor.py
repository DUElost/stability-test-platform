"""Agent recovery / lease-lost helpers extracted from ``main`` (#736).

Keeps ADR-0019 recovery action execution and lease-lost occupancy cleanup
testable without importing the 800+ line ``main()`` process entrypoint.
``main`` wires these helpers; production install layout resolves them via
``agent.recovery_executor`` (same package as ``patrol_recovery``).
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Dict, List, Optional, Set

from .api_client import sync_recovery

logger = logging.getLogger(__name__)

def _make_local_worker_token(
    job_id: int,
    fencing_token: str,
    *,
    prefix: str = "worker",
) -> str:
    """Return stable local ownership for one ``(job_id, fencing_token)`` pair."""
    digest = hashlib.sha256(
        f"{int(job_id)}\0{fencing_token}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{int(job_id)}-{digest}"


def _cleanup_after_lease_lost(
    *,
    job_id: int,
    device_id: Optional[int],
    active_jobs_lock: Any,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    active_job_tokens: Dict[int, str],
    active_device_owner: Optional[Dict[int, int]] = None,
    local_db: Any,
    keep_device_slot: bool = False,
) -> None:
    """lease 丢失后的内存状态清理。

    ``keep_device_slot=True``（#799）：已派发 abort（runner 正在收尾）时保留设备
    占位与归属，直到 worker 真正退出（``JobRunnerState.release`` 的归属感知补偿
    负责清理）——把「本机不再驱动该设备」从「清理内存集合」推迟到「进程已死」的
    硬保证成立时，避免设备立即回池被另一台 host 领走（跨 host 双驱）。
    """
    with active_jobs_lock:
        active_job_ids.discard(job_id)
        active_job_tokens.pop(job_id, None)
        if device_id is not None and not keep_device_slot:
            active_device_ids.discard(device_id)
            if active_device_owner is not None and active_device_owner.get(device_id) == job_id:
                active_device_owner.pop(device_id, None)


def handle_lease_lost(
    *,
    job_id: int,
    device_id: Optional[int],
    job_runner_state: Any,
    coordinator: Any,
    active_jobs_lock: Any,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    active_job_tokens: Dict[int, str],
    active_device_owner: Optional[Dict[int, int]] = None,
    local_db: Any,
) -> bool:
    """lease 丢失统一处理（#799 / ADR-0026 Step 5b）。

    顺序：① 对仍在跑的 job 派发 abort（``request_abort`` → runner.cancel /
    killpg，同时置 ``_is_aborted()`` 真，供 permit 等待者退出）→ ② 清理内存
    状态（abort 已派发时保留设备占位，待 worker 退出再清）→ ③ 唤醒等待者。

    返回 abort 是否派发（True = 有活跃 job 被 cancel）。
    """
    abort_dispatched = False
    if job_runner_state is not None:
        try:
            abort_dispatched = bool(job_runner_state.request_abort(job_id))
        except Exception:
            logger.exception("on_lease_lost_abort_failed", extra={"job_id": job_id})
    try:
        _cleanup_after_lease_lost(
            job_id=job_id,
            device_id=device_id,
            active_jobs_lock=active_jobs_lock,
            active_job_ids=active_job_ids,
            active_device_ids=active_device_ids,
            active_job_tokens=active_job_tokens,
            active_device_owner=active_device_owner,
            local_db=local_db,
            keep_device_slot=abort_dispatched,
        )
    except Exception:
        logger.exception("on_lease_lost_cleanup_failed", extra={
            "job_id": job_id,
            "reason": "external_cleanup_exception",
        })
    coordinator.cancel_waiting_job(job_id)
    return abort_dispatched
    # 保留本地 active_job 记录，等待设备重连或 agent 重启时走 recovery/sync 恢复。


def _cleanup_after_job_exit(
    *,
    job_id: int,
    fencing_token: str,
    local_worker_token: str = "",
    active_jobs_lock: Any,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    active_job_tokens: Dict[int, str],
    active_device_owner: Optional[Dict[int, int]] = None,
    lease_renewer: Any,
    local_db: Any,
) -> None:
    """Worker/JobSession 退出后的统一清理。

    正常完成时删除本地 active_job；若该 job 已先因 lease_lost 从活跃集合移除，
    则仅清 runtime 占位，保留本地记录等待 recovery/sync。
    """
    effective_worker_token = local_worker_token or fencing_token
    device_id = lease_renewer.clear_fencing_token_if_current(
        job_id,
        fencing_token,
        effective_worker_token,
    )
    with active_jobs_lock:
        current_token = active_job_tokens.get(job_id, "")
        job_was_active = job_id in active_job_ids and (
            not effective_worker_token or current_token == effective_worker_token
        )
        if job_was_active:
            active_job_ids.discard(job_id)
            active_job_tokens.pop(job_id, None)
            if device_id is not None:
                active_device_ids.discard(device_id)
                if active_device_owner is not None and active_device_owner.get(device_id) == job_id:
                    active_device_owner.pop(device_id, None)
    if job_was_active:
        # #1005: 仅当终态事实到达可靠落点（远端 ack 或 outbox 持久化，均留
        # job_terminal_outbox 行）才删除恢复依据；complete_job 双故障（HTTP
        # 与 enqueue 都失败）时无行——删掉会让终态静默丢失（远端无终态、
        # 本地无 outbox、恢复入口也被删）。
        if local_db is None or local_db.has_terminal_fact(job_id):
            local_db.delete_active_job(job_id)
        else:
            logger.error(
                "active_job_kept_terminal_lost job=%d — no terminal fact "
                "durably recorded; recovery record retained", job_id,
            )


def _rollback_failed_claim(
    *,
    jid: int,
    fencing_token: str,
    local_worker_token: str,
    device_id: Optional[int],
    active_jobs_lock: Any,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    active_job_tokens: Dict[int, str],
    active_device_owner: Optional[Dict[int, int]] = None,
    lease_renewer: Any,
    local_db: Any,
) -> None:
    """R07-F13 (#1013): compensate a claim whose local SQLite registration failed
    mid-way (device placeholder already pre-allocated, fencing token possibly
    set). Roll back only what this attempt claimed so the device is not poisoned
    and the same pending batch can keep going; the next tick / recovery re-claims
    anything the server still sees as pending."""
    released_device: Optional[int] = None
    try:
        released_device = lease_renewer.clear_fencing_token_if_current(
            jid,
            fencing_token,
            local_worker_token=local_worker_token,
        )
    except Exception:
        logger.exception("rollback_fencing_clear_failed job=%d", jid)
    with active_jobs_lock:
        active_job_ids.discard(jid)
        active_job_tokens.pop(jid, None)
    own_device = released_device if released_device is not None else device_id
    if own_device is not None:
        with active_jobs_lock:
            active_device_ids.discard(own_device)
            if active_device_owner is not None and active_device_owner.get(own_device) == jid:
                active_device_owner.pop(own_device, None)
    # Registration never reached SQLite; drop any stray row so a claimed-but-
    # never-started job is not treated as live by the next recovery pass.
    try:
        local_db.delete_active_job(jid)
    except Exception:
        logger.exception("rollback_delete_active_failed job=%d", jid)


def trigger_recovery_sync_on_device_reconnect(
    *,
    reconnected_serials: List[str],
    local_db: Any,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    boot_id: str,
    execute_actions: Any,
) -> bool:
    """Device reconnect hook: re-run recovery sync when local active jobs still exist.

    Returns True when recovery is settled (completed, or nothing to do) —
    callers clear their reconnect marks; False when recovery was needed but
    this attempt failed — callers must keep the marks so the next heartbeat
    re-attempts without requiring another plug/unplug (#1009).
    """
    if not reconnected_serials:
        return True

    persisted_jobs = local_db.get_active_jobs()
    if not persisted_jobs:
        logger.info(
            "recovery_skip_reconnect_no_local_jobs serials=%s",
            ",".join(reconnected_serials),
        )
        return True

    matched_jobs = [
        job
        for job in persisted_jobs
        if job.get("device_serial") and job["device_serial"] in reconnected_serials
    ]
    if not matched_jobs:
        logger.info(
            "recovery_skip_reconnect_no_serial_match serials=%s active_jobs=%d",
            ",".join(reconnected_serials),
            len(persisted_jobs),
        )
        return True

    logger.info(
        "recovery_reconnect_triggered serials=%s matched_jobs=%d active_jobs=%d",
        ",".join(reconnected_serials),
        len(matched_jobs),
        len(persisted_jobs),
    )
    return run_recovery_sync_if_needed(
        local_db=local_db,
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        execute_actions=execute_actions,
        active_jobs=matched_jobs,
    )


def execute_recovery_actions_impl(
    resp: dict,
    active_jobs_by_id: dict,
    lease_renewer: Any,
    local_db: Any,
    outbox_drain: Any,
    register_active_job: Any,
    resume_job: Any = None,
    abort_local_job: Any = None,
) -> None:
    """ADR-0019 Phase 3a: execute recovery actions (module-level for testability).

    Durable terminal outbox takes priority over RESUME (#1004 / R07-F03): a job
    that already finished locally must upload its terminal payload, never
    relaunch a worker (rotated fencing tokens would also reject the original
    terminal fact).
    """
    job_actions = resp.get("actions", [])
    outbox_actions = resp.get("outbox_actions", [])

    upload_terminal_ids = {
        int(a["job_id"])
        for a in outbox_actions
        if a.get("action") == "UPLOAD_TERMINAL" and a.get("job_id") is not None
    }
    local_pending_ids: set[int] = set()
    try:
        pending_raw = local_db.get_pending_outbox()
        if isinstance(pending_raw, (list, tuple)):
            for entry in pending_raw:
                if isinstance(entry, dict) and entry.get("job_id") is not None:
                    local_pending_ids.add(int(entry["job_id"]))
    except Exception:
        logger.exception("recovery_list_pending_outbox_failed")
    terminal_priority_ids = upload_terminal_ids | local_pending_ids

    # Prefer persisted terminal upload before any RESUME (#1004).
    if outbox_actions or local_pending_ids:
        has_upload = bool(upload_terminal_ids) or bool(local_pending_ids)
        if has_upload:
            try:
                flushed = outbox_drain.drain_sync()
            except Exception:
                # #1176: 吞错后静默提前 return 会让本轮 RESUME/ABORT/CLEANUP
                # 全丢，而上层 run_recovery_sync_if_needed 无从得知仍记成功、
                # 心跳据此清掉重连标记——恢复被推迟到下次物理插拔。
                # 上抛 → 上层转 False，保留标记由心跳周期自动重试。
                logger.exception("recovery_outbox_flush_failed")
                raise
            logger.info("recovery_outbox_flushed count=%d", flushed)

        still_pending: set[int] = set()
        try:
            pending_after = local_db.get_pending_outbox()
            if isinstance(pending_after, (list, tuple)):
                still_pending = {
                    int(e["job_id"])
                    for e in pending_after
                    if isinstance(e, dict) and e.get("job_id") is not None
                }
        except Exception:
            logger.exception("recovery_relist_pending_outbox_failed")

        for a in outbox_actions:
            jid = a["job_id"]
            action = a["action"]
            if action == "UPLOAD_TERMINAL":
                if jid not in still_pending:
                    local_db.delete_active_job(jid)
                    lease_renewer.clear_fencing_token(jid)
                else:
                    logger.warning("recovery_upload_terminal_still_pending job=%d", jid)
            elif action == "NOOP":
                local_db.delete_active_job(jid)

        # Local pending without a matching outbox_action still needs active cleanup
        # after a successful drain.
        for jid in local_pending_ids:
            if jid not in still_pending:
                local_db.delete_active_job(jid)
                lease_renewer.clear_fencing_token(jid)

    for a in job_actions:
        jid = a["job_id"]
        action = a["action"]
        if action == "RESUME":
            if jid in terminal_priority_ids:
                logger.warning(
                    "recovery_resume_skipped_pending_terminal_outbox job=%d",
                    jid,
                )
                continue
            token = a.get("fencing_token", "")
            # Defense-in-depth: a RESUME without a dict job_payload cannot re-enter
            # JobSession, so the watcher would never re-attach and the job would
            # become a zombie active record. Backend now guarantees RESUME carries
            # a payload (job-row-missing → ABORT_LOCAL); skip registration if it
            # somehow doesn't, and let the next recovery round reconcile.
            if not isinstance(a.get("job_payload"), dict):
                logger.warning(
                    "recovery_resume_missing_payload job=%d — skipping register (backend will reconcile)",
                    jid,
                )
                continue
            persisted_job = active_jobs_by_id.get(jid) or {}
            device_serial = (a.get("device_serial") or persisted_job.get("device_serial") or "").strip()
            local_worker_token = _make_local_worker_token(
                jid, token, prefix="resume",
            )
            register_active_job(
                jid,
                token,
                a.get("device_id"),
                device_serial,
                local_worker_token,
            )
            if resume_job is not None:
                resumed_payload = dict(a["job_payload"])
                resumed_payload["id"] = jid
                if a.get("device_id") is not None:
                    resumed_payload["device_id"] = a["device_id"]
                if device_serial:
                    resumed_payload["device_serial"] = device_serial
                if token:
                    resumed_payload["fencing_token"] = token
                resumed_payload["local_worker_token"] = local_worker_token
                # T3: mark as recovery-resumed so the watcher re-attach is observable
                resumed_payload["recovery_resumed"] = True
                # #1176: submit 失败不能吞——register 已把 job 标活跃、续租持有
                # fencing token，worker 却未启动；静默继续会让上层清标记，该
                # job 成僵尸直到 patrol 兜底。上抛保留重连标记，下一心跳周期
                # 重试（register 幂等，resume 重发即可自愈）。
                resume_job(resumed_payload)
            logger.info(
                "recovery_resume job=%d token=%s worker=%s",
                jid,
                token[:8] if token else "",
                local_worker_token,
            )
        elif action == "CLEANUP":
            if abort_local_job is not None:
                abort_local_job(jid)
            local_db.delete_active_job(jid)
            lease_renewer.clear_fencing_token(jid)
            logger.warning("recovery_cleanup job=%d reason=%s", jid, a.get("reason"))
        elif action == "ABORT_LOCAL":
            if abort_local_job is not None:
                abort_local_job(jid)
            local_db.delete_active_job(jid)
            lease_renewer.clear_fencing_token(jid)
            logger.warning("recovery_abort_local job=%d reason=%s", jid, a.get("reason"))


def _coerce_recovery_interval(raw: str | None, default: float = 60.0) -> float:
    """#1710：recovery sync 周期间隔——非法/非有限值回落默认，再夹下限 5s。

    ``float("nan")`` 不抛且 ``nan < 5`` 为 False，若直接落入 ``Event.wait(nan)``
    会立即返回 → 周期线程满速空转打控制面。
    """
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = float(text)
    except (TypeError, ValueError):
        logger.warning(
            "invalid STP_RECOVERY_SYNC_INTERVAL_SECONDS=%r; using default %.1f",
            raw,
            default,
        )
        return default
    if not math.isfinite(value):
        logger.warning(
            "non-finite STP_RECOVERY_SYNC_INTERVAL_SECONDS=%r; using default %.1f",
            raw,
            default,
        )
        return default
    if value < 5:
        return 5.0
    return value


def run_recovery_sync_if_needed(
    local_db: Any,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    boot_id: str,
    execute_actions: Any,
    active_jobs: Optional[List[dict]] = None,
) -> bool:
    """ADR-0019 Phase 3a: check local persisted state and sync with Backend if needed.

    Returns True when recovery state is settled (fully reconciled, or there
    is nothing to reconcile); False when a transient failure left recovery
    incomplete — callers must keep their retry state so a later heartbeat /
    reconnect re-attempts (#1009).
    """
    try:
        persisted_jobs = active_jobs if active_jobs is not None else local_db.get_active_jobs()
        pending_outbox = local_db.get_pending_outbox()
        if not (persisted_jobs or pending_outbox):
            logger.info("recovery_skip_no_persisted_state")
            return True
        resp = sync_recovery(
            api_url, host_id, agent_instance_id, boot_id,
            active_jobs=persisted_jobs,
            pending_outbox=pending_outbox,
        )
        if resp is None:
            logger.warning("recovery_sync_failed_http — retry state kept")
            return False
        execute_actions(resp, {j["job_id"]: j for j in persisted_jobs})
        logger.info(
            "recovery_sync_complete active_jobs=%d outbox=%d",
            len(persisted_jobs), len(pending_outbox),
        )
        return True
    except Exception:
        logger.exception("recovery_sync_failed_continuing — retry state kept")
        return False

