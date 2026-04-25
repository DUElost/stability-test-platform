"""Pipeline execution adapter used by the agent job runner."""

import logging
import os
from typing import Any, Callable, Dict, Optional

import requests

from .config import get_run_log_dir
from .pipeline_engine import PipelineEngine
from .ws_client import AgentWSClient

logger = logging.getLogger(__name__)


def execute_pipeline_run(
    pipeline_def: Dict[str, Any],
    run_id: int,
    device_serial: str,
    adb: Any,
    api_url: str,
    host_id: str,
    ws_client: Optional[Any] = None,
    mq_producer: Optional[Any] = None,
    tool_registry: Optional[Any] = None,
    script_registry: Optional[Any] = None,
    local_db: Optional[Any] = None,
    is_aborted: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Execute one claimed job through PipelineEngine and normalize its result."""
    log_dir = get_run_log_dir(run_id)
    os.makedirs(log_dir, exist_ok=True)

    own_ws = False
    if ws_client is None:
        agent_secret = os.getenv("AGENT_SECRET", "")
        ws_client = AgentWSClient(api_url, host_id, agent_secret)
        ws_client.connect()
        own_ws = True

    agent_secret = os.getenv("AGENT_SECRET", "")

    def http_step_fallback(rid, sid, status, **kwargs):
        url = f"{api_url}/api/v1/agent/jobs/{rid}/steps/{sid}/status"
        payload = {"status": status}
        for key in ("started_at", "finished_at", "exit_code", "error_message"):
            if key in kwargs and kwargs[key] is not None:
                value = kwargs[key]
                if hasattr(value, "isoformat"):
                    value = value.isoformat()
                payload[key] = value
        headers = {"X-Agent-Secret": agent_secret} if agent_secret else {}
        try:
            requests.post(url, json=payload, headers=headers, timeout=10)
        except Exception as exc:
            logger.warning("HTTP step status fallback failed: %s", exc)

    engine = PipelineEngine(
        adb=adb,
        serial=device_serial,
        run_id=run_id,
        log_dir=log_dir,
        ws_client=ws_client,
        http_fallback=http_step_fallback,
        mq_producer=mq_producer,
        tool_registry=tool_registry,
        script_registry=script_registry,
        local_db=local_db,
        api_url=api_url,
        agent_secret=agent_secret,
        is_aborted=is_aborted,
    )

    try:
        result = engine.execute(pipeline_def)
    finally:
        if own_ws:
            ws_client.disconnect()

    status = "FINISHED" if result.success else "FAILED"
    if not result.success and isinstance(getattr(result, "metadata", None), dict):
        if result.metadata.get("termination_reason") == "abort":
            status = "CANCELED"

    return {
        "status": status,
        "exit_code": result.exit_code,
        "error_code": None,
        "error_message": result.error_message,
        "log_summary": None,
        "artifact": result.artifact,
    }
