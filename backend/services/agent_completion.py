"""Agent Job 终态完成业务逻辑（#1520 垂直切片：agent_api complete 线）。

覆盖 ``/jobs/{job_id}/complete``：终态映射、fencing 回放幂等、UNKNOWN grace
late-complete、状态机迁移、watcher 摘要、lease 释放、聚合与 post_completion
入队。路由退化为 ``ok(await complete_agent_job(...))``。

共享的 ``get_valid_runtime_lease`` 亦下沉于此（heartbeat / extend_lock 经路由
re-export 继续使用）。``resume_expired_lease_for_recovery`` 复用
``agent_recovery``。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.audit_writer import record_audit_async
from backend.core.metrics import (
    post_completion_enqueue_failed_total,
    record_disarm_result,
    record_reconciler_dirs_abandoned,
    record_reconciler_dirs_oversized_skipped,
    record_reconciler_skip_unchanged,
    record_watcher_capability,
    set_reconciler_unresolved_dirs,
    set_watcher_reconciler_present,
)
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.job import JobInstance, StepTrace
from backend.realtime.socketio_server import broadcast_run_job_update
from backend.services.aggregator import PlanAggregator
from backend.services.agent_recovery import resume_expired_lease_for_recovery
from backend.services.errors import BadRequest, Conflict, NotFound
from backend.services.lease_manager import release_lease
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)

_TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_RUN_TO_JOB: Dict[str, JobStatus] = {
    "RUNNING":   JobStatus.RUNNING,
    "FINISHED":  JobStatus.COMPLETED,
    "COMPLETED": JobStatus.COMPLETED,
    "FAILED":    JobStatus.FAILED,
    "CANCELED":  JobStatus.ABORTED,
    "CANCELLED": JobStatus.ABORTED,
    "ABORTED":   JobStatus.ABORTED,
}


# ── #3723 U2 / ADR-0061 D4：解除结果落库与兼容推导 ──────────────────────────
# U1 合入后改为从 backend.agent.contracts.device_return 导入下列常量与 FAMILY_STEPS。
# 本单元不得创建/修改 device_return.py（§3.1 表后说明）。

_DISARM_RESULT_EXECUTED_UNVERIFIED = "executed_unverified"
_DISARM_RESULT_FAILED = "failed"
_DISARM_RESULT_NOT_EXECUTED = "not_executed"
_DISARM_RESULT_INCOMPLETE = "incomplete"

_SIGNAL_EXIT_CODES = {143, -15, 137, -9}

# §3.0 第 2 条 FAMILY_STEPS（与 U1 契约同形的字面量副本，仅供控制面推导）
_FAMILY_STEPS: Dict[str, Dict[str, tuple]] = {
    "power-cycle": {
        "footprint": ("powercycle_setup",),
        "disarm": ("powercycle_finish",),
        "disarm_onboarded": ("powercycle_disarm",),
    },
    "sleep": {
        "footprint": ("sleep_setup",),
        "disarm": ("sleep_finish",),
        "disarm_onboarded": ("sleep_finish",),
    },
    "mtbf": {
        "footprint": ("mtbf_setup",),
        "disarm": ("mtbf_finish",),
        "disarm_onboarded": ("mtbf_finish",),
    },
    "gpu": {
        "footprint": ("gpu_setup",),
        "disarm": ("gpu_finish",),
        "disarm_onboarded": ("gpu_finish",),
    },
    "monkey": {
        "footprint": ("monkey_setup", "monkey_launch", "monkey_test"),
        "disarm": ("monkey_teardown",),
        "disarm_onboarded": ("monkey_teardown",),
    },
}


def _script_name_from_action(action: Any) -> str:
    if not isinstance(action, str) or not action.startswith("script:"):
        return ""
    return action.split(":", 1)[1].strip()


def _pipeline_steps_by_stage(pipeline_def: Any, stage: str) -> list[dict]:
    if not isinstance(pipeline_def, dict):
        return []
    lifecycle = pipeline_def.get("lifecycle") or {}
    if not isinstance(lifecycle, dict):
        return []
    steps = lifecycle.get(stage) or []
    if isinstance(steps, dict):
        steps = steps.get("steps") or []
    if not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, dict)]


def _step_id_to_script(pipeline_def: Any) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for stage in ("init", "teardown", "patrol"):
        for step in _pipeline_steps_by_stage(pipeline_def, stage):
            sid = step.get("step_id") or ""
            script = _script_name_from_action(step.get("action"))
            if sid and script:
                mapping[str(sid)] = script
    return mapping


def _family_for_script(script_name: str, role: str) -> Optional[str]:
    if not script_name:
        return None
    for family, spec in _FAMILY_STEPS.items():
        names = spec.get(role) or ()
        if script_name in names:
            return family
        if role == "disarm" and script_name in (spec.get("disarm_onboarded") or ()):
            return family
    return None


def _map_trace_to_disarm_result(status: Any, exit_code: Any) -> str:
    """§3.0 第 6 条：COMPLETED / FAILED(+signal) / 其它 FAILED / 无 trace。"""
    st = str(status or "").strip().upper()
    if st in {"COMPLETED", "FINISHED", "SUCCESS", "OK"}:
        return _DISARM_RESULT_EXECUTED_UNVERIFIED
    if st in {"FAILED", "ERROR", "ABORTED", "CANCELLED", "CANCELED"}:
        try:
            code = int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            code = None
        if code in _SIGNAL_EXIT_CODES:
            return _DISARM_RESULT_INCOMPLETE
        return _DISARM_RESULT_FAILED
    return _DISARM_RESULT_NOT_EXECUTED


def _trace_field(tr: Any, name: str) -> Any:
    if isinstance(tr, dict):
        return tr.get(name)
    return getattr(tr, name, None)


def _is_disarm_terminal_status(status: Any) -> bool:
    st = str(status or "").strip().upper()
    return st in {
        "COMPLETED", "FINISHED", "SUCCESS", "OK",
        "FAILED", "ERROR", "ABORTED", "CANCELLED", "CANCELED",
    }


def _trace_occurrence_key(tr: Any, index: int) -> tuple:
    """Occurrence order: original_ts first; stable tie-breakers only."""
    original_ts = _trace_field(tr, "original_ts")
    if isinstance(original_ts, datetime):
        ts = original_ts if original_ts.tzinfo is not None else original_ts.replace(tzinfo=timezone.utc)
        ts_key: tuple = (0, ts.timestamp())
    elif original_ts is None:
        ts_key = (1, 0.0)
    else:
        ts_key = (1, 0.0)
    created_at = _trace_field(tr, "created_at")
    if isinstance(created_at, datetime):
        ca = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=timezone.utc)
        ca_key: tuple = (0, ca.timestamp())
    else:
        ca_key = (1, 0.0)
    trace_id = _trace_field(tr, "id")
    try:
        id_key = (0, int(trace_id))
    except (TypeError, ValueError):
        id_key = (1, index)
    return (ts_key, ca_key, id_key, index)


def derive_disarm_results_from_traces(
    pipeline_def: Any,
    traces: list[Any],
) -> Dict[str, Dict[str, Any]]:
    """从 teardown 步骤 trace 按 FAMILY_STEPS 推导 disarm_results（#3723 §3.0 第 6 条）。

    足迹：init 中 FAMILY_STEPS.footprint 步骤一旦出现任意 trace 即视为已建立。
    解除：按 original_ts 发生顺序选取该专项 teardown 解除脚本的终态；迟到的
    STARTED/RUNNING 不得覆盖其后发生的 COMPLETED/FAILED（§3.3.2 B）。
    无终态证据 → not_executed。
    """
    id_to_script = _step_id_to_script(pipeline_def)
    footprint: set[str] = set()
    disarm_events: Dict[str, list[tuple[tuple, dict]]] = {}

    for index, tr in enumerate(traces or []):
        stage = _trace_field(tr, "stage")
        step_id = _trace_field(tr, "step_id")
        status = _trace_field(tr, "status")
        exit_code = _trace_field(tr, "exit_code")
        script = id_to_script.get(str(step_id or ""), "")
        if not script:
            continue
        if stage == "init":
            fam = _family_for_script(script, "footprint")
            if fam:
                footprint.add(fam)
        elif stage == "teardown":
            fam = _family_for_script(script, "disarm")
            if fam:
                event = {
                    "status": status,
                    "exit_code": exit_code,
                    "step_id": step_id,
                }
                disarm_events.setdefault(fam, []).append(
                    (_trace_occurrence_key(tr, index), event)
                )

    families = set(footprint) | set(disarm_events)
    out: Dict[str, Dict[str, Any]] = {}
    for fam in sorted(families):
        events = sorted(disarm_events.get(fam) or [], key=lambda item: item[0])
        terminal: dict | None = None
        for _key, event in events:
            if _is_disarm_terminal_status(event.get("status")):
                terminal = event  # later original_ts wins
        if terminal is None:
            entry = {
                "result": _DISARM_RESULT_NOT_EXECUTED,
                "step_id": "",
                "exit_code": None,
                "readback_summary": "",
            }
        else:
            entry = {
                "result": _map_trace_to_disarm_result(
                    terminal.get("status"), terminal.get("exit_code"),
                ),
                "step_id": str(terminal.get("step_id") or ""),
                "exit_code": terminal.get("exit_code"),
                "readback_summary": "",
            }
        out[fam] = entry
    return out


def normalize_disarm_results(raw: Any) -> Optional[Dict[str, Dict[str, Any]]]:
    """Accept Agent-reported disarm_results; return None when absent/empty/invalid."""
    if not isinstance(raw, dict) or not raw:
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for family, entry in raw.items():
        if not isinstance(family, str) or not family:
            continue
        if isinstance(entry, str):
            out[family] = {
                "result": entry,
                "step_id": "",
                "exit_code": None,
                "readback_summary": "",
            }
            continue
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, str) or not result:
            continue
        out[family] = {
            "result": result,
            "step_id": str(entry.get("step_id") or ""),
            "exit_code": entry.get("exit_code"),
            "readback_summary": str(entry.get("readback_summary") or ""),
            **({"reason": entry["reason"]} if entry.get("reason") else {}),
        }
    return out or None


def apply_disarm_results(job: JobInstance, results: Dict[str, Dict[str, Any]]) -> None:
    """Persist disarm_results on the job and count metrics. Never touch status_reason."""
    job.disarm_results = results
    for family, entry in results.items():
        record_disarm_result(family, entry.get("result") or "unknown")


class _RunCompleteIn(BaseModel):
    update: Dict[str, Any]
    artifact: Optional[Dict[str, Any]] = None
    # Watcher 摘要回填（来自 Agent JobSession.summary.to_complete_payload）
    # 字段形态参考 backend/agent/contracts/watcher_contracts.py WatcherSummaryPayload
    # 可选：watcher_id / watcher_started_at / watcher_stopped_at / watcher_capability / log_signal_count / watcher_stats
    watcher_summary: Optional[Dict[str, Any]] = None
    # ADR-0061 D4 / #3723 §3.3 U2：按专项解除结果；缺失时控制面从 teardown trace 推导
    disarm_results: Optional[Dict[str, Any]] = None
    fencing_token: str  # ADR-0019 Phase 2b: 必填


async def get_valid_runtime_lease(
    db: AsyncSession,
    job: JobInstance,
    fencing_token: str,
    allowed_job_statuses: set[str] | None = None,
) -> Optional[DeviceLease]:
    """Validate runtime lease for token-gated operations (Phase 4b).

    Returns the valid ACTIVE lease, or None if any check fails.
    Checks (all must pass):
      1. ACTIVE lease exists for (device_id, job_id, JOB)
      2. fencing_token matches
      3. expires_at > now (B: expired lease rejects all runtime ops)
      4. job.status == RUNNING (C: whitelist, not blacklist)
    """
    now = datetime.now(timezone.utc)
    lease = (await db.execute(
        select(DeviceLease).where(
            DeviceLease.device_id == job.device_id,
            DeviceLease.job_id == job.id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    )).scalars().first()

    if lease is None:
        return None
    if lease.fencing_token != fencing_token:
        return None
    # B: expired lease rejects all runtime operations
    expires_at = _as_utc(lease.expires_at)
    if expires_at is None or expires_at <= now:
        return None
    # C: only RUNNING jobs may perform runtime operations
    allowed_statuses = allowed_job_statuses or {JobStatus.RUNNING.value}
    if job.status not in allowed_statuses:
        return None
    return lease

def apply_watcher_summary(job: JobInstance, summary: Dict[str, Any]) -> None:
    """把 Agent 回传的 watcher_summary 回填到 JobInstance 字段。

    字段来源契约：backend/agent/contracts/watcher_contracts.py WatcherSummaryPayload
    只在 summary 非空字段存在时覆写，保持旧字段。
    """
    started = summary.get("watcher_started_at")
    if started:
        try:
            job.watcher_started_at = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("watcher_summary.watcher_started_at invalid: %r", started)

    stopped = summary.get("watcher_stopped_at")
    if stopped:
        try:
            job.watcher_stopped_at = datetime.fromisoformat(stopped.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("watcher_summary.watcher_stopped_at invalid: %r", stopped)

    capability = summary.get("watcher_capability")
    if capability:
        job.watcher_capability = str(capability)[:32]

    # log_signal_count 由 /log-signals 端点累加，这里不覆写
    # 但若 Agent 侧有权威计数（watcher_summary.log_signal_count），作为下限同步
    count = summary.get("log_signal_count")
    if isinstance(count, int) and count > (job.log_signal_count or 0):
        job.log_signal_count = count

def bridge_reconciler_metrics(host_id: Optional[str], summary: Dict[str, Any]) -> None:
    """M0/Task2: 把 Agent 进程内的 reconciler 计数桥接到中心 /metrics。

    Agent 没有独立的 Prometheus /metrics 暴露面,reconciler 的
    `reconciler_skip_unchanged_total` 只在 Agent 进程的本地 registry 自增、永不被抓取。
    为让 M4 监控盘可见,Agent 通过 complete 通道带出整个 Job 生命周期累计的
    `reconciler_stats.ticks_skipped_unchanged`,后端在 Job *首次*进入终态时一次性
    按该累计值自增中心计数器(每个 Job 仅贡献一次 → 计数器单调正确)。

    `reconciler_burst_mode_active` 是运行期实时 gauge(Job 结束时恒为 0),无法通过
    终态快照有意义地带出 → 仍仅 Agent 进程内,详见 §2.3 文档说明。
    """
    # #2394③：在位标记与 stats 字典无关（MTK/UNISOC 通用），先于门禁处理。
    presence = str(summary.get("platform_reconciler") or "").strip()
    if presence:
        set_watcher_reconciler_present(str(host_id or "unknown"), presence.split(":", 1)[0])

    stats = summary.get("reconciler_stats")
    if not isinstance(stats, dict):
        return
    skipped = stats.get("ticks_skipped_unchanged")
    if isinstance(skipped, int) and skipped > 0:
        record_reconciler_skip_unchanged(str(host_id or "unknown"), amount=skipped)

    # #2394①：UNISOC 专属可见面。三个字段 MTK 路恒 0（共享 ReconcilerStats），
    # 无门禁地桥接会把「MTK 完成」误刷成「UNISOC 全清」——以 presence 平台段把关。
    if not presence.upper().startswith("UNISOC"):
        return
    host = str(host_id or "unknown")
    abandoned = stats.get("dirs_abandoned")
    if isinstance(abandoned, int) and abandoned > 0:
        record_reconciler_dirs_abandoned(host, amount=abandoned)
    oversized = stats.get("dirs_oversized_skipped")
    if isinstance(oversized, int) and oversized > 0:
        record_reconciler_dirs_oversized_skipped(host, amount=oversized)
    unresolved = stats.get("unresolved_dirs")
    if isinstance(unresolved, int) and unresolved >= 0:
        set_reconciler_unresolved_dirs(host, unresolved)

async def complete_agent_job(
    db: AsyncSession,
    job_id: int,
    payload: _RunCompleteIn,
) -> dict:
    """Transition job to a terminal status."""
    job = (await db.execute(
        select(JobInstance)
        .where(JobInstance.id == job_id)
        .with_for_update()
    )).scalars().first()
    if job is None:
        raise NotFound("job not found")

    raw = str(payload.update.get("status", "FAILED")).strip().upper()
    # #779: 未知状态串不得经 .get(..., FAILED) 伪装成真实失败。
    if raw not in _RUN_TO_JOB:
        raise BadRequest({
            "code": "INVALID_TERMINAL_STATUS",
            "requested_status": raw,
        })
    target = _RUN_TO_JOB[raw]
    if target not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED}:
        raise BadRequest({
            "code": "INVALID_TERMINAL_STATUS",
            "requested_status": raw,
        })

    completion_fact = {
        "update": payload.update,
        "artifact": payload.artifact,
        "watcher_summary": payload.watcher_summary,
        "disarm_results": payload.disarm_results,
    }
    payload_digest = hashlib.sha256(
        json.dumps(
            completion_fact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    # Terminal replay is strictly read-only.  Validate against the historical
    # lease token so a stale/cross-host Agent cannot rewrite completion facts.
    already_terminal = job.status in _TERMINAL
    if already_terminal:
        historical_lease = (await db.execute(
            select(DeviceLease)
            .where(
                DeviceLease.device_id == job.device_id,
                DeviceLease.job_id == job.id,
                DeviceLease.lease_type == LeaseType.JOB.value,
            )
            .order_by(DeviceLease.id.desc())
        )).scalars().first()
        if (
            historical_lease is None
            or historical_lease.fencing_token != payload.fencing_token
        ):
            current_status = job.status
            await record_audit_async(
                db,
                action="stale_job_completion_rejected",
                resource_type="job_instance",
                resource_id=job.id,
                details={
                    "plan_run_id": job.plan_run_id,
                    "current_status": current_status,
                    "reason": "historical_fencing_token_mismatch",
                },
                username="agent",
            )
            await db.commit()
            raise Conflict({
                "code": "STALE_COMPLETION_TOKEN",
                "current_status": current_status,
            })
        expected_digest = job.terminal_payload_digest
        if expected_digest is None:
            historical_trace = (await db.execute(
                select(StepTrace).where(
                    StepTrace.job_id == job_id,
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
            )).scalars().first()
            if historical_trace is not None and historical_trace.output:
                try:
                    historical_fact = json.loads(historical_trace.output)
                    current_legacy_fact = {
                        "update": payload.update,
                        "artifact": payload.artifact,
                    }
                    expected_digest = hashlib.sha256(
                        json.dumps(
                            historical_fact,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    replay_digest = hashlib.sha256(
                        json.dumps(
                            current_legacy_fact,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                except (TypeError, ValueError, json.JSONDecodeError):
                    replay_digest = ""
            else:
                replay_digest = ""
        else:
            replay_digest = payload_digest
        if not expected_digest or not secrets.compare_digest(
            expected_digest, replay_digest,
        ):
            current_status = job.status
            await record_audit_async(
                db,
                action="terminal_payload_conflict",
                resource_type="job_instance",
                resource_id=job.id,
                details={
                    "plan_run_id": job.plan_run_id,
                    "current_status": current_status,
                    "expected_digest": expected_digest,
                    "received_digest": replay_digest or payload_digest,
                },
                username="agent",
            )
            await db.commit()
            raise Conflict({
                "code": "TERMINAL_PAYLOAD_CONFLICT",
                "current_status": current_status,
            })
        current_status = job.status
        await db.rollback()
        return {"job_id": job_id, "status": current_status, "idempotent": True}
    else:
        valid_lease = None
        if job.status == JobStatus.UNKNOWN.value:
            # Explicit late-terminal reconciliation: a matching grace-held
            # token may atomically restore UNKNOWN→RUNNING before completion.
            # This preserves the terminal outbox fact without adding the
            # forbidden UNKNOWN→COMPLETED edge to the state machine.
            candidate = (await db.execute(
                select(DeviceLease)
                .where(
                    DeviceLease.device_id == job.device_id,
                    DeviceLease.job_id == job.id,
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .with_for_update()
            )).scalars().first()
            if (
                candidate is not None
                and secrets.compare_digest(
                    candidate.fencing_token, payload.fencing_token,
                )
                and await resume_expired_lease_for_recovery(
                    db,
                    candidate,
                    job,
                    candidate.agent_instance_id,
                    datetime.now(timezone.utc),
                )
            ):
                JobStateMachine.transition(
                    job, JobStatus.RUNNING, "late_completion_recovery",
                )
                valid_lease = candidate
        if valid_lease is None:
            valid_lease = await get_valid_runtime_lease(
                db, job, payload.fencing_token,
            )
        if valid_lease is None:
            raise Conflict({
                "code": "INVALID_OR_EXPIRED_FENCING_TOKEN",
                "current_status": job.status,
            })

    transition_from_status = job.status
    try:
        JobStateMachine.transition(job, target, payload.update.get("error_message") or "")
    except InvalidTransitionError as exc:
        raise Conflict({
            "code": "INVALID_JOB_TRANSITION",
            "message": str(exc),
            "current_status": job.status,
            "requested_status": target.value,
        }) from exc

    # 持久化一次性完成快照（log_summary + artifact），为新链路报告读取提供数据闭环。
    snapshot = {
        "update": payload.update,
        "artifact": payload.artifact,
    }
    snapshot_output = json.dumps(snapshot, ensure_ascii=False)
    now_ts = datetime.now(timezone.utc)
    existing_snapshot = (
        await db.execute(
            select(StepTrace).where(
                StepTrace.job_id == job_id,
                StepTrace.step_id == "__job__",
                StepTrace.event_type == "RUN_COMPLETE",
            )
        )
    ).scalars().first()
    if existing_snapshot is None:
        db.add(
            StepTrace(
                job_id=job_id,
                step_id="__job__",
                stage="post_process",
                status=target.value,
                event_type="RUN_COMPLETE",
                output=snapshot_output,
                error_message=payload.update.get("error_message"),
                trace_event_id=f"terminal:{job_id}:{payload_digest}",
                original_ts=now_ts,
                created_at=datetime.now(timezone.utc),
            )
        )

    job.ended_at = datetime.now(timezone.utc)
    job.terminal_payload_digest = payload_digest

    # Watcher 摘要回填（来自 Agent JobSession.summary.to_complete_payload）
    # 字段契约见 backend/agent/contracts/watcher_contracts.py WatcherSummaryPayload
    if payload.watcher_summary:
        apply_watcher_summary(job, payload.watcher_summary)

    # ADR-0061 D4 / #3723 §3.3 U2：解除结果落库（不得写入 status_reason）
    reported = normalize_disarm_results(payload.disarm_results)
    if reported is None:
        existing_traces = (
            await db.execute(
                select(StepTrace)
                .where(StepTrace.job_id == job_id)
                .order_by(
                    StepTrace.original_ts.asc().nulls_last(),
                    StepTrace.id.asc(),
                )
            )
        ).scalars().all()
        reported = derive_disarm_results_from_traces(job.pipeline_def, existing_traces)
    if reported:
        apply_disarm_results(job, reported)

    # ADR-0052 D5（#3244）：原「ABORTED 分支逐 Job 读改写
    # run_context.abort_requested.acknowledged_job_ids」已删除——ACK 语义由
    # Job 终态推导（全仓无消费方；abort 入口的初始空数组与保留合并仍在
    # plan_run_abort，历史 JSON 不动）。R523 对照：ABORTED 波里每条终态曾
    # 在此额外排队一次 plan_run 行锁 + 数组重写。

    if job.status in _TERMINAL:
        # M0/Task2: 仅在首次终态桥接 reconciler 计数,避免 outbox 重试重复计数。
        if payload.watcher_summary:
            bridge_reconciler_metrics(job.host_id, payload.watcher_summary)
            # M4/T4-2: 终态时按 watcher_capability 自增一次(覆盖率监控盘);
            # 与 reconciler 桥接同处 not-already_terminal 守卫内,每 Job 仅计一次。
            record_watcher_capability(job.watcher_capability or "unknown")
        # Release before aggregation.  Chain dispatch inherits the same devices
        # and must observe them as free when the terminal transaction commits.
        released = await release_lease(db, job.device_id, job_id, LeaseType.JOB)
        if not released:
            logger.warning("release_lease_miss device=%s job=%s", job.device_id, job_id)
        await db.flush()
        await record_audit_async(
            db,
            action="job_terminalized",
            resource_type="job_instance",
            resource_id=job.id,
            details={
                "plan_run_id": job.plan_run_id,
                "from_status": transition_from_status,
                "to_status": target.value,
                "payload_digest": payload_digest,
                "lease_released": bool(released),
            },
            username="agent",
        )
        await PlanAggregator.on_job_terminal(job, db)

    await db.commit()

    if job.status in _TERMINAL:
        # ── SocketIO push: job completed/failed → notify PlanRun subscribers ──
        # 父 Run 的 `plan_run_status` 不在此处发：ADR-0052 D1 后终态由聚合者异步判定，
        # 提交后立即读父行恒非终态（dead code）；统一由 plan_run_finalization
        # 在父终态 commit 后补发（#3384）。
        await broadcast_run_job_update(job.plan_run_id, job_id, job.status)

        try:
            from backend.core.task_queue import get_queue
            from saq import Job as SaqJob

            await get_queue().enqueue(
                SaqJob(
                    function="post_completion_task",
                    kwargs={"job_id": job_id},
                    key=f"pc:{job_id}",
                    timeout=120,
                    retries=3,
                    retry_delay=5.0,
                    retry_backoff=True,
                )
            )
        except Exception as e:
            post_completion_enqueue_failed_total.inc()
            logger.error("post_completion enqueue failed for job %d: %s", job_id, e)

    return {"job_id": job_id, "status": job.status}

# 路由 / 既有测试用的私有名别名。
_get_valid_runtime_lease = get_valid_runtime_lease
_apply_watcher_summary = apply_watcher_summary
_bridge_reconciler_metrics = bridge_reconciler_metrics
