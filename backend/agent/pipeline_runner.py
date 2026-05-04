"""Pipeline execution adapter used by the agent job runner."""

import logging
import os
from typing import Any, Callable, Dict, Optional

from .config import get_run_log_dir
from .pipeline_engine import PipelineEngine

logger = logging.getLogger(__name__)


def execute_pipeline_run(
    pipeline_def: Dict[str, Any],
    run_id: int,
    device_serial: str,
    adb: Any,
    api_url: str,
    host_id: str,
    mq_producer: Optional[Any] = None,
    tool_registry: Optional[Any] = None,
    script_registry: Optional[Any] = None,
    local_db: Optional[Any] = None,
    is_aborted: Optional[Callable[[], bool]] = None,
    fencing_token: str = "",
) -> Dict[str, Any]:
    """Execute one claimed job through PipelineEngine and normalize its result."""
    log_dir = get_run_log_dir(run_id)
    os.makedirs(log_dir, exist_ok=True)

    agent_secret = os.getenv("AGENT_SECRET", "")

    engine = PipelineEngine(
        adb=adb,
        serial=device_serial,
        run_id=run_id,
        log_dir=log_dir,
        mq_producer=mq_producer,
        tool_registry=tool_registry,
        script_registry=script_registry,
        local_db=local_db,
        api_url=api_url,
        agent_secret=agent_secret,
        is_aborted=is_aborted,
        fencing_token=fencing_token,
    )

    result = engine.execute(pipeline_def)

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
