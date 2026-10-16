"""Centralised job / lease timeout defaults (main-chain fragility §2.4–2.9).

Production defaults are unchanged. Override via env; dev-only shorter defaults
apply when ``ENV`` is set and not ``production`` (and ``TESTING!=1``).

| Variable | Production default | Dev default (optional) |
|----------|-------------------|------------------------|
| ``DISPATCHED_TIMEOUT_SECONDS`` / ``RUN_DISPATCHED_TIMEOUT_SECONDS`` | 120 | 120 |
| ``RUNNING_HEARTBEAT_TIMEOUT_SECONDS`` / ``RUN_HEARTBEAT_TIMEOUT_SECONDS`` | 900 | 900 |
| ``PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS`` | 300 | 180 |
| ``STEP_TRACE_STALL_SECONDS`` | 3600 | 3600 |
| ``HOST_HEARTBEAT_TIMEOUT_SECONDS`` | 300 | 300 |
| ``UNKNOWN_GRACE_SECONDS`` | 300 | 300 |
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

from backend.core.adr0026_params import DEFAULTS as _ADR0026_DEFAULTS


def _is_non_production_runtime() -> bool:
    env = os.getenv("ENV", "").strip().lower()
    if not env or env == "production":
        return False
    if os.getenv("TESTING") == "1":
        return False
    return True


def _int_env(*names: str, production_default: int, dev_default: int | None = None) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip() != "":
            return int(raw)
    if _is_non_production_runtime() and dev_default is not None:
        return dev_default
    return production_default


# PENDING job never claimed by Agent → recycler marks FAILED.
# Does NOT apply while the same host still has RUNNING jobs (capacity queue).
DISPATCHED_TIMEOUT_SECONDS = _int_env(
    "DISPATCHED_TIMEOUT_SECONDS",
    "RUN_DISPATCHED_TIMEOUT_SECONDS",
    production_default=120,
)

# RUNNING job lost heartbeat → UNKNOWN (lease stays; reconciler grace follows)
RUNNING_HEARTBEAT_TIMEOUT_SECONDS = _int_env(
    "RUNNING_HEARTBEAT_TIMEOUT_SECONDS",
    "RUN_HEARTBEAT_TIMEOUT_SECONDS",
    production_default=900,
)

# RUNNING patrol-phase jobs (pipeline has patrol + patrol signal present)
PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS = _int_env(
    "PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS",
    production_default=300,
    dev_default=180,
)

# Patrol cadence stall threshold shared by recycler and API deadline projection.
PATROL_STALL_MULTIPLIER = _int_env(
    "PATROL_STALL_MULTIPLIER",
    production_default=3,
)

# RUNNING job with no step_trace activity beyond this window → recycler UNKNOWN (#3061).
# 0 = disable Pass #2c. Latest trace must be terminal (not STARTED).
STEP_TRACE_STALL_SECONDS = _int_env(
    "STEP_TRACE_STALL_SECONDS",
    production_default=3600,
)

PRECHECK_QUEUE_STALE_SECONDS = _int_env(
    "PRECHECK_QUEUE_STALE_SECONDS",
    production_default=90,
)
PRECHECK_ACTIVE_STALE_SECONDS = _int_env(
    "PRECHECK_ACTIVE_STALE_SECONDS",
    production_default=180,
)

ABORT_ACK_GRACE_SECONDS = _int_env(
    "ABORT_REAPER_GRACE_SECONDS",
    production_default=60,
)

# Host HTTP heartbeat freshness / OFFLINE threshold (#1518).
# Single source for session_watchdog + hosts/devices/reachability/settings.
# Default 300 aligns with ADR-0025 / .env.example (not the legacy 120 in
# session_watchdog alone).
HOST_HEARTBEAT_TIMEOUT_SECONDS = _int_env(
    "HOST_HEARTBEAT_TIMEOUT_SECONDS",
    production_default=300,
)

# UNKNOWN job grace before lease release + FAILED
UNKNOWN_GRACE_SECONDS = _int_env(
    "UNKNOWN_GRACE_SECONDS",
    production_default=300,
)


def has_patrol_lifecycle(pipeline_def: object) -> bool:
    if not isinstance(pipeline_def, dict):
        return False
    patrol = (pipeline_def.get("lifecycle") or {}).get("patrol")
    return isinstance(patrol, dict)


def job_in_patrol_phase(
    *,
    patrol_cycle_count: int | None,
    last_patrol_heartbeat_at: object,
    current_patrol_step: object,
) -> bool:
    return (
        (patrol_cycle_count or 0) > 0
        or last_patrol_heartbeat_at is not None
        or bool(current_patrol_step)
    )


def _aware_dt(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _patrol_interval_seconds(pipeline_def: object) -> int | None:
    if not isinstance(pipeline_def, dict):
        return None
    patrol = (pipeline_def.get("lifecycle") or {}).get("patrol")
    if not isinstance(patrol, dict):
        return None
    interval = patrol.get("interval_seconds")
    if isinstance(interval, int) and interval > 0:
        return interval
    return None


def running_heartbeat_timeout_seconds(
    job: object,
    *,
    patrol_stall_multiplier: int | None = None,
) -> int:
    """Graded RUNNING timeout: patrol-active jobs may use a separate window."""
    pipeline_def = getattr(job, "pipeline_def", None)
    if has_patrol_lifecycle(pipeline_def) and job_in_patrol_phase(
        patrol_cycle_count=getattr(job, "patrol_cycle_count", None),
        last_patrol_heartbeat_at=getattr(job, "last_patrol_heartbeat_at", None),
        current_patrol_step=getattr(job, "current_patrol_step", None),
    ):
        interval = _patrol_interval_seconds(pipeline_def)
        multiplier = patrol_stall_multiplier or PATROL_STALL_MULTIPLIER
        timeout = PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS
        if interval is not None:
            timeout = max(timeout, interval * multiplier)

        updated_at = _aware_dt(getattr(job, "updated_at", None))
        next_retry_at = _aware_dt(getattr(job, "next_retry_at", None))
        if (
            interval is not None
            and updated_at is not None
            and next_retry_at is not None
        ):
            retry_delay = max(0, int((next_retry_at - updated_at).total_seconds()))
            timeout = max(timeout, retry_delay + interval * multiplier)
        return timeout
    return RUNNING_HEARTBEAT_TIMEOUT_SECONDS


# ── #3723 §3.3.2 A / ADR-0061 D3.8：解除宽限与 abort 主体纯判断 ──────────────
# abort reaper 与通用 recycler 共用；无 DB / 无副作用。ORM 薄壳保留在
# device_lease_reconciler（兼容既有测试导入）。

EXECUTING_DISARM = "EXECUTING_DISARM"

DISARM_SCRIPT_NAMES: frozenset[str] = frozenset({
    "powercycle_finish",
    "powercycle_disarm",
    "sleep_finish",
    "mtbf_finish",
    "gpu_finish",
    "monkey_teardown",
})


def coordinator_progress_freshness_seconds(
    interval_seconds: int | None = None,
) -> int:
    """两个协调心跳周期（#3723 §3.0 第 5 条 / §3.3.2 A）。"""
    if interval_seconds is None:
        interval_seconds = int(_ADR0026_DEFAULTS.coordinator_heartbeat_interval_seconds)
    return int(interval_seconds) * 2


def parse_abort_at(value: object) -> datetime | None:
    """把 ``abort_requested.at`` 解析为 aware datetime；坏值 → None（不误杀）。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def abort_request_covers_job(run_context: object, job_id: int) -> bool:
    """run 主体是否覆盖该 job（名单缺失/空 = 历史形态，视为覆盖）。"""
    ctx = run_context if isinstance(run_context, dict) else {}
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


def host_abort_clock_at(run_context: object, host_id: str | None) -> object:
    """host 主体时钟 ``abort_requested_hosts[host_id].at``；缺失 → None。"""
    if not host_id:
        return None
    ctx = run_context if isinstance(run_context, dict) else {}
    hosts = ctx.get("abort_requested_hosts")
    if not isinstance(hosts, dict):
        return None
    clock = hosts.get(host_id)
    if not isinstance(clock, dict):
        return None
    return clock.get("at")


def abort_reap_clock(
    run_context: object,
    job_id: int,
    host_id: str | None,
) -> tuple[datetime | None, str | None]:
    """按被请求主体取宽限时钟；并存时取更早者。返回 ``(at, subject)``。"""
    ctx = run_context if isinstance(run_context, dict) else {}
    clocks: list[tuple[datetime, str]] = []
    if abort_request_covers_job(ctx, job_id):
        abort = ctx.get("abort_requested")
        raw = abort.get("at") if isinstance(abort, dict) else None
        run_at = parse_abort_at(raw)
        if run_at is not None:
            clocks.append((run_at, "run"))
    host_at = parse_abort_at(host_abort_clock_at(ctx, host_id))
    if host_at is not None:
        clocks.append((host_at, "host"))
    if not clocks:
        return None, None
    at, subject = min(clocks, key=lambda item: item[0])
    return at, subject


def disarm_step_timeout_seconds(pipeline_def: object) -> int | None:
    """解除步骤墙钟上限：teardown 中已启用解除脚本 timeout_seconds 的最大值。"""
    if not isinstance(pipeline_def, dict):
        return None
    lifecycle = pipeline_def.get("lifecycle") or {}
    if not isinstance(lifecycle, dict):
        return None
    teardown = lifecycle.get("teardown") or []
    if not isinstance(teardown, list):
        return None
    timeouts: list[int] = []
    for step in teardown:
        if not isinstance(step, dict):
            continue
        if step.get("enabled") is False:
            continue
        action = step.get("action") or ""
        if not isinstance(action, str) or not action.startswith("script:"):
            continue
        script = action.split(":", 1)[1].strip()
        if script not in DISARM_SCRIPT_NAMES:
            continue
        raw = step.get("timeout_seconds")
        if raw is None:
            continue
        try:
            timeouts.append(int(raw))
        except (TypeError, ValueError):
            continue
    return max(timeouts) if timeouts else None


def disarm_grace_defers_reap(
    job: object,
    abort_at: datetime,
    now: datetime,
    *,
    progress_freshness_seconds: int | None = None,
) -> bool:
    """True → 本轮不回收（解除中且进度新鲜，且未超 abort_at+timeout 上限）。"""
    if getattr(job, "execution_state", None) != EXECUTING_DISARM:
        return False
    progress = _aware_dt(getattr(job, "last_progress_at", None))
    if progress is None:
        return False
    freshness = (
        progress_freshness_seconds
        if progress_freshness_seconds is not None
        else coordinator_progress_freshness_seconds()
    )
    if (now - progress).total_seconds() > freshness:
        return False
    timeout = disarm_step_timeout_seconds(getattr(job, "pipeline_def", None))
    if timeout is None:
        return False
    ceiling = abort_at + timedelta(seconds=timeout)
    return now < ceiling


def disarm_grace_protects(
    job: object,
    run_context: object,
    now: datetime,
    *,
    progress_freshness_seconds: int | None = None,
) -> bool:
    """RUNNING 解除宽限是否成立：有效 abort 主体 + EXECUTING_DISARM + 新鲜进度。"""
    job_id = getattr(job, "id", None)
    if job_id is None:
        return False
    abort_at, _subject = abort_reap_clock(
        run_context, int(job_id), getattr(job, "host_id", None),
    )
    if abort_at is None:
        return False
    return disarm_grace_defers_reap(
        job,
        abort_at,
        now,
        progress_freshness_seconds=progress_freshness_seconds,
    )
