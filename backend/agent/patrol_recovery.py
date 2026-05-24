"""One-shot recovery/sync when patrol heartbeat receives JOB_NOT_RUNNING."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)


def build_patrol_job_not_running_handler(
    *,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    boot_id: str,
    local_db: Any,
    execute_actions: Callable[[dict, dict], None],
) -> Callable[[int], None]:
    """Return a callback that triggers recovery/sync at most once per job_id."""

    attempted: set[int] = set()
    lock = threading.Lock()

    def handler(job_id: int) -> None:
        with lock:
            if job_id in attempted:
                logger.debug("patrol_recovery_skip_duplicate job=%d", job_id)
                return
            attempted.add(job_id)

        logger.info("patrol_recovery_sync_triggered job=%d", job_id)
        # Lazy import avoids circular dependency with agent.main at module load.
        from backend.agent.main import run_recovery_sync_if_needed

        run_recovery_sync_if_needed(
            local_db=local_db,
            api_url=api_url,
            host_id=host_id,
            agent_instance_id=agent_instance_id,
            boot_id=boot_id,
            execute_actions=execute_actions,
        )

    return handler
