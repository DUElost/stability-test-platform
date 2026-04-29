"""Agent API 客户端 —— 任务认领、状态上报、终态提交。

提取自 backend.agent.main，供 main.py barrel re-export。
"""

import logging
import os
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


def _get_agent_secret() -> str:
    return os.getenv("AGENT_SECRET", "")


def _get_post_retries() -> int:
    return int(os.getenv("AGENT_POST_RETRIES", "3"))


def _get_post_retry_base_delay() -> float:
    return float(os.getenv("AGENT_POST_RETRY_BASE_DELAY", "1"))


def fetch_pending_jobs(api_url: str, host_id: str) -> List[Dict[str, Any]]:
    """Claim pending jobs via POST /agent/jobs/claim (D1: 统一 claim 路径).

    Backend 在 claim 响应中注入 device_serial + watcher_policy，供 JobSession 启动使用。
    """
    agent_secret = _get_agent_secret()
    headers = {"X-Agent-Secret": agent_secret} if agent_secret else {}
    resp = requests.post(
        f"{api_url}/api/v1/agent/jobs/claim",
        json={"host_id": host_id, "capacity": 10},
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


def _post_with_retry(
    url: str, payload: Dict[str, Any], context: str, timeout: int = 10
) -> None:
    agent_secret = _get_agent_secret()
    post_retries = _get_post_retries()
    retry_base_delay = _get_post_retry_base_delay()

    headers = {"X-Agent-Secret": agent_secret} if agent_secret else {}
    last_error: Optional[Exception] = None
    for attempt in range(1, post_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return
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


def complete_job(
    api_url: str,
    job_id: int,
    payload: Dict[str, Any],
    fencing_token: str,
    local_db=None,
) -> None:
    """Report job terminal state. Writes to local outbox first for durability."""
    complete_payload = _build_complete_payload(payload, fencing_token)

    if local_db is not None:
        try:
            local_db.enqueue_terminal(job_id, complete_payload)
        except Exception as e:
            logger.warning("outbox_enqueue_failed job=%d: %s", job_id, e)

    try:
        _post_with_retry(
            f"{api_url}/api/v1/agent/jobs/{job_id}/complete",
            complete_payload,
            context=f"job_complete:{job_id}",
        )
        if local_db is not None:
            try:
                local_db.ack_terminal(job_id)
            except Exception:
                pass
    except Exception:
        if local_db is not None:
            logger.warning(
                "complete_job_deferred_to_outbox job=%d", job_id,
            )
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
