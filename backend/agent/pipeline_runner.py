"""Pipeline execution adapter used by the agent job runner."""

import logging
import os
from typing import Any, Callable, Dict, Optional

from .config import get_run_log_dir
from .patrol_heartbeat_uploader import PatrolHeartbeatUploader
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
    script_registry: Optional[Any] = None,
    local_db: Optional[Any] = None,
    is_aborted: Optional[Callable[[], bool]] = None,
    fencing_token: str = "",
    on_job_not_running_recovery: Optional[Callable[[int], None]] = None,
    watcher_capability: Optional[str] = None,
    patrol_cycle_checkpoint_store: Optional[Any] = None,
    on_engine_started: Optional[Callable[[PipelineEngine], None]] = None,
    on_engine_stopped: Optional[Callable[[PipelineEngine], None]] = None,
    # ADR-0026 Step 5b: per-host scheduler + per-job state reporting
    operation_scheduler: Any = None,
    coordinator: Any = None,
    device_id: Optional[int] = None,
    plan_run_host_id: Optional[int] = None,
    barrier_total: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute one claimed job through PipelineEngine and normalize its result."""
    log_dir = get_run_log_dir(run_id)
    os.makedirs(log_dir, exist_ok=True)

    agent_secret = os.getenv("AGENT_SECRET", "")

    # ADR-0022: per-run heartbeat uploader for patrol stage aggregation.
    # Stateless on the Agent side; safe to instantiate per job.
    patrol_heartbeat = PatrolHeartbeatUploader(
        api_url=api_url,
        agent_secret=agent_secret,
        on_job_not_running=on_job_not_running_recovery,
    )

    engine = PipelineEngine(
        adb=adb,
        serial=device_serial,
        run_id=run_id,
        log_dir=log_dir,
        mq_producer=mq_producer,
        script_registry=script_registry,
        local_db=local_db,
        api_url=api_url,
        agent_secret=agent_secret,
        is_aborted=is_aborted,
        fencing_token=fencing_token,
        patrol_heartbeat_uploader=patrol_heartbeat,
        watcher_capability=watcher_capability,
        patrol_cycle_checkpoint_store=patrol_cycle_checkpoint_store,
        operation_scheduler=operation_scheduler,
        coordinator=coordinator,
        device_id=device_id,
        plan_run_host_id=plan_run_host_id,
        barrier_total=barrier_total,
    )

    if patrol_cycle_checkpoint_store is not None:
        row = patrol_cycle_checkpoint_store.get_for_recovery(str(run_id))
        if row is not None:
            engine.set_patrol_cycle_resume(row.checkpoint)
            logger.info(
                "patrol_checkpoint_resume job_id=%s cycle=%s",
                run_id,
                row.checkpoint.get("cycle"),
            )

    if on_engine_started is not None:
        on_engine_started(engine)
    try:
        result = engine.execute(pipeline_def)
    finally:
        if on_engine_stopped is not None:
            on_engine_stopped(engine)

    status = "FINISHED" if result.success else "FAILED"
    if not result.success and isinstance(getattr(result, "metadata", None), dict):
        # ADR-0022: manual_exit shares the abort-style terminal semantics
        # (skip teardown + status=ABORTED) — surface as CANCELED upstream.
        if result.metadata.get("termination_reason") in ("abort", "manual_exit"):
            status = "CANCELED"

    return {
        "status": status,
        "exit_code": result.exit_code,
        "error_code": None,
        "error_message": result.error_message,
        "log_summary": None,
        # ADR-0025 方案 C: 运行日志不再上送 15.4，Agent 本地保留。
        # 实时查看：SocketIO → 控制面 log_writer；事后取证：SSH POST /api/v1/agent/logs。
        "artifact": None,
    }
