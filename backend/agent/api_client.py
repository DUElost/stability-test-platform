"""Agent API 客户端 —— 任务认领、状态上报、终态提交。

提取自 backend.agent.main，供 main.py barrel re-export。
"""

import logging
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

RUN_TERMINAL_STATUS_MAP = {
    "COMPLETED": "FINISHED",
    "FINISHED": "FINISHED",
    "FAILED": "FAILED",
    "CANCELED": "CANCELED",
    "CANCELLED": "CANCELED",
    "ABORTED": "CANCELED",
}


class TerminalReportLostError(Exception):
    """Terminal fact reached neither the backend nor the local outbox.

    #1005: complete_job 双故障（HTTP 失败 + enqueue_terminal 失败）。调用方
    不得把结果伪装成已 deferred——active_job_registry 记录是唯一的恢复
    依据，必须保留到远端或 outbox 能确认。
    """


def _get_agent_secret() -> str:
    return os.getenv("AGENT_SECRET", "")


def _get_post_retries() -> int:
    return int(os.getenv("AGENT_POST_RETRIES", "3"))


def _get_post_retry_base_delay() -> float:
    return float(os.getenv("AGENT_POST_RETRY_BASE_DELAY", "1"))


# ── #3242 终态上报削峰 ───────────────────────────────────────────────────────
# R523 现场：490 个 RUNNING 同时回传终态，峰值 55 req/s；其中**每个事实被送了
# 3.35 次**（1644 次请求 / 490 个事实）——首发的线程内重试把中心舱壁削掉的峰又打了
# 回去。终态事实在 HTTP 之前已经落本地 SQLite outbox，**首发的价值只是抢一次
# 「当场确认」**，失败由 drainer 补送即可，不值得用重试去换。
#
# 三项旋钮（均可 env 覆盖）：
# - `AGENT_TERMINAL_UPLOAD_CONCURRENCY`（默认 2）：同刻在飞的终态 POST 上限。
#   单机设备多（R523 现场 max 23），abort/收尾会让它们同时收尾——上限把单机的
#   瞬时连接需求压到中心舱壁（默认 8）以内；
# - `AGENT_TERMINAL_ABORT_JITTER_SECONDS`（默认 5）：**abort 产生的终态**在首发前
#   随机等 0–5s，削掉「同一瞬间几十台设备一起回传」的相位；
# - `AGENT_POST_RETRIES`：仅在**outbox 入队也失败**时才回退到线程内重试
#   （此时没有补送落点，多试两次比丢掉终态事实便宜）。
_TERMINAL_UPLOAD_SEMAPHORE = threading.BoundedSemaphore(
    max(1, int(os.getenv("AGENT_TERMINAL_UPLOAD_CONCURRENCY", "2")))
)


def _terminal_abort_jitter_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("AGENT_TERMINAL_ABORT_JITTER_SECONDS", "5")))
    except ValueError:
        return 5.0


def _terminal_status_of(complete_payload: Dict[str, Any]) -> str:
    """终态状态串（大小写归一）。

    形状：`_build_complete_payload` 把状态放在 `update.status`（顶层兼容旧形态）。
    注意 `ABORTED` 经 `RUN_TERMINAL_STATUS_MAP` 归一成 **CANCELED**——abort 族
    三个写法都要认（抖动判据用）。
    """
    update = complete_payload.get("update")
    raw = (
        update.get("status")
        if isinstance(update, dict)
        else complete_payload.get("status")
    )
    return str(raw or "").strip().upper()


#: abort 族状态（`_build_complete_payload` 把 ABORTED 归一成 CANCELED）。
_ABORT_STATUSES = frozenset({"ABORTED", "CANCELED", "CANCELLED"})


def fetch_pending_jobs(api_url: str, host_id: str, agent_instance_id: str = "",
                       capacity: int = 2) -> List[Dict[str, Any]]:
    """Claim pending jobs via POST /agent/jobs/claim (D1: 统一 claim 路径).

    Backend 在 claim 响应中注入 device_serial + watcher_policy，供 JobSession 启动使用。
    *capacity* should be the Agent's actual available slots to avoid claiming
    more jobs than the thread pool can execute (orphaned RUNNING jobs).
    """
    from . import __version__ as agent_version

    agent_secret = _get_agent_secret()
    headers = {"X-Agent-Secret": agent_secret} if agent_secret else {}
    resp = requests.post(
        f"{api_url}/api/v1/agent/jobs/claim",
        json={
            "host_id": host_id,
            "capacity": capacity,
            "agent_instance_id": agent_instance_id,
            "agent_version": agent_version,
        },
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"] or []
    return payload


def fetch_pending_runs(api_url: str, host_id: str) -> List[Dict[str, Any]]:
    """Backward-compatible alias for callers not yet migrated to job naming."""
    return fetch_pending_jobs(api_url, host_id)


def sync_recovery(
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    boot_id: str,
    active_jobs: List[Dict[str, Any]],
    pending_outbox: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """ADR-0019 Phase 3a: sync recovery state with Backend.

    Returns recovery actions dict {"actions": [...], "outbox_actions": [...]} or None on failure.
    """
    agent_secret = _get_agent_secret()
    headers = {"X-Agent-Secret": agent_secret} if agent_secret else {}
    try:
        resp = requests.post(
            f"{api_url}/api/v1/agent/recovery/sync",
            json={
                "host_id": host_id,
                "agent_instance_id": agent_instance_id,
                "boot_id": boot_id,
                "active_jobs": active_jobs,
                "pending_outbox": pending_outbox,
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("data", payload)
    except Exception as exc:
        logger.warning("recovery_sync_failed: %s", exc)
        return None


def _post_with_retry(
    url: str,
    payload: Dict[str, Any],
    context: str,
    timeout: int = 10,
    attempts: Optional[int] = None,
) -> None:
    """POST（带线程内指数退避重试）。

    ``attempts`` 覆盖 `AGENT_POST_RETRIES`：终态上报传 1（#3242——事实已落本地
    outbox，首发只抢一次「当场确认」，重试会把中心舱壁削掉的峰再打回去）。
    """
    agent_secret = _get_agent_secret()
    post_retries = max(1, int(attempts if attempts is not None else _get_post_retries()))
    retry_base_delay = _get_post_retry_base_delay()

    headers = {"X-Agent-Secret": agent_secret} if agent_secret else {}
    last_error: Optional[Exception] = None
    for attempt in range(1, post_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return
        except requests.HTTPError as exc:
            # 409 Conflict is a definitive rejection (invalid fencing_token,
            # state transition conflict).  Don't retry — let the outbox handle it.
            if exc.response is not None and exc.response.status_code == 409:
                logger.warning(
                    "agent_post_conflict",
                    extra={"context": context, "status": 409, "detail": str(exc)},
                )
                raise
            last_error = exc
            if attempt >= post_retries:
                logger.warning(
                    "agent_post_failed",
                    extra={"context": context, "attempts": attempt, "error": str(exc)},
                )
                raise
            delay = retry_base_delay * (2 ** (attempt - 1))
            logger.warning(
                "agent_post_retry",
                extra={
                    "context": context,
                    "attempt": attempt,
                    "next_delay_seconds": delay,
                    "error": str(exc),
                },
            )
            time.sleep(delay)
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= post_retries:
                logger.warning(
                    "agent_post_failed",
                    extra={"context": context, "attempts": attempt, "error": str(exc)},
                )
                raise
            delay = retry_base_delay * (2 ** (attempt - 1))
            logger.warning(
                "agent_post_retry",
                extra={
                    "context": context,
                    "attempt": attempt,
                    "next_delay_seconds": delay,
                    "error": str(exc),
                },
            )
            time.sleep(delay)
    if last_error:
        raise last_error


def update_job(api_url: str, job_id: int, payload: Dict[str, Any]) -> None:
    _post_with_retry(
        f"{api_url}/api/v1/agent/jobs/{job_id}/heartbeat",
        payload,
        context=f"job_heartbeat:{job_id}",
    )


def update_run(api_url: str, run_id: int, payload: Dict[str, Any]) -> None:
    """Backward-compatible alias for job heartbeat updates."""
    update_job(api_url, run_id, payload)


def _build_complete_payload(payload: Dict[str, Any], fencing_token: str) -> Dict[str, Any]:
    """Build the normalized payload for the /complete endpoint."""
    raw_status = str(payload.get("status", "FAILED")).upper()
    normalized_status = RUN_TERMINAL_STATUS_MAP.get(raw_status, "FAILED")
    complete_payload: Dict[str, Any] = {
        "update": {
            "status": normalized_status,
            "exit_code": payload.get("exit_code"),
            "error_code": payload.get("error_code"),
            "error_message": payload.get("error_message"),
            "log_summary": payload.get("log_summary"),
        },
        "fencing_token": fencing_token,
    }
    artifact = payload.get("artifact")
    if isinstance(artifact, dict):
        complete_payload["artifact"] = artifact
    watcher_summary = payload.get("watcher_summary")
    if isinstance(watcher_summary, dict):
        complete_payload["watcher_summary"] = watcher_summary
    return complete_payload


def _log_terminal_overload_deferral(job_id: int, exc: Exception) -> None:
    """429/503 是**预期背压**（不是故障）：INFO 一行并交给 outbox（#3242）。

    为什么值得单记：把「中心让我们慢下来」与「真故障」分开，才能让现场日志回答
    「削峰有没有生效」；`Retry-After` 一并留下，便于与 outbox 的退避对账。
    """
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status not in (429, 503):
        return
    retry_after = "-"
    headers = getattr(resp, "headers", None)
    if headers:
        retry_after = headers.get("Retry-After") or "-"
    logger.info(
        "complete_job_overload_deferred_to_outbox job=%d status=%d retry_after=%s",
        job_id,
        status,
        retry_after,
    )


def complete_job(
    api_url: str,
    job_id: int,
    payload: Dict[str, Any],
    fencing_token: str,
    local_db=None,
) -> None:
    """Report job terminal state. Writes to local outbox first for durability.

    Outcome triage (#1005) 不变：
    - 远端确认（HTTP ok）: ack outbox（若有）后返回;
    - 仅本地持久化（enqueue ok + HTTP 失败）: 打 deferred 日志返回，outbox
      drain 会补送;
    - 两者均失败: raise :class:`TerminalReportLostError`——不得伪称已
      deferred；本地 active 记录由清理路径保留作恢复依据。

    #3242 削峰（R523 现场：490 个终态同时回传，且每个事实平均送了 3.35 次）：
    - **首发只打一次**——事实在 HTTP 之前已落 outbox，重试只会把中心舱壁削掉的峰
      打回去；仅当 outbox 入队也失败（没有补送落点）才回退到 `AGENT_POST_RETRIES`；
    - 同刻在飞的终态 POST ≤ `AGENT_TERMINAL_UPLOAD_CONCURRENCY`（默认 2）；
    - abort 族终态在首发前随机等 0–`AGENT_TERMINAL_ABORT_JITTER_SECONDS`（默认 5）秒。
    """
    complete_payload = _build_complete_payload(payload, fencing_token)

    outbox_ok = False
    if local_db is not None:
        try:
            local_db.enqueue_terminal(job_id, complete_payload)
            outbox_ok = True
        except Exception as e:
            logger.warning("outbox_enqueue_failed job=%d: %s", job_id, e)

    status = _terminal_status_of(complete_payload)
    jitter_cap = _terminal_abort_jitter_seconds()
    if status in _ABORT_STATUSES and jitter_cap > 0:
        jitter = random.uniform(0.0, jitter_cap)
        logger.info(
            "complete_job_abort_jitter job=%d status=%s delay=%.2fs",
            job_id,
            status,
            jitter,
        )
        # 事实已在本地 outbox；这里只错峰，不承担持久性。
        time.sleep(jitter)

    attempts = 1 if outbox_ok else _get_post_retries()
    try:
        with _TERMINAL_UPLOAD_SEMAPHORE:
            _post_with_retry(
                f"{api_url}/api/v1/agent/jobs/{job_id}/complete",
                complete_payload,
                context=f"job_complete:{job_id}",
                attempts=attempts,
            )
        if local_db is not None and outbox_ok:
            try:
                local_db.ack_terminal(job_id)
            except Exception as exc:
                # #739 面②：ack 失败会让该 terminal 留在 outbox、后续被 drainer 重发
                # （服务端按 fencing/digest 幂等），但现场需要能解释"为什么又发了一次"。
                logger.warning("outbox_ack_terminal_failed job=%d: %s", job_id, exc)
    except Exception as exc:
        _log_terminal_overload_deferral(job_id, exc)
        if local_db is not None and outbox_ok:
            logger.warning(
                "complete_job_deferred_to_outbox job=%d", job_id,
            )
        elif local_db is not None:
            logger.error(
                "complete_job_terminal_lost job=%d — HTTP failed and outbox "
                "enqueue failed; keeping active recovery record", job_id,
            )
            raise TerminalReportLostError(
                f"terminal fact for job {job_id} reached neither backend nor outbox"
            ) from exc
        else:
            raise


def complete_run(
    api_url: str,
    run_id: int,
    payload: Dict[str, Any],
    fencing_token: str,
    local_db=None,
) -> None:
    """Backward-compatible alias for job terminal reporting."""
    complete_job(api_url, run_id, payload, fencing_token=fencing_token, local_db=local_db)
