"""Backend Redis Stream consumer.

Reads from stp:status → persists StepTrace / JobInstance status to DB.
Reads from stp:logs   → broadcasts step logs to WebSocket subscribers.
Monitors stp:status lag → writes backpressure key (read by heartbeat endpoint).

All functions run as asyncio tasks, started in FastAPI lifespan.
"""

import asyncio
import json
import logging
from datetime import datetime

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

STATUS_STREAM = "stp:status"
STATUS_GROUP = "server-consumer"
CONSUMER_NAME = "server-0"
BLOCK_MS = 1000
BATCH_SIZE = 50

LOG_STREAM = "stp:logs"
LOG_GROUP = "log-consumer"
LOG_CONSUMER = "log-0"
LOG_BLOCK_MS = 1000
LOG_BATCH_SIZE = 200

_LAG_HIGH = int(__import__("os").getenv("BACKPRESSURE_LAG_THRESHOLD", "5000"))
_LAG_LOW = int(__import__("os").getenv("BACKPRESSURE_RELEASE_THRESHOLD", "500"))
_BP_KEY = "stp:backpressure:log_rate_limit"
_BP_CHECK_INTERVAL = 5  # seconds


async def consume_status_stream(redis_client: aioredis.Redis) -> None:
    """Continuously read stp:status and persist events to DB."""
    logger.info("MQ consumer started")
    while True:
        try:
            results = await redis_client.xreadgroup(
                groupname=STATUS_GROUP,
                consumername=CONSUMER_NAME,
                streams={STATUS_STREAM: ">"},
                count=BATCH_SIZE,
                block=BLOCK_MS,
            )
            if not results:
                continue
            for _stream, messages in results:
                for msg_id, fields in messages:
                    await _process_status_message(fields)
                    try:
                        await redis_client.xack(STATUS_STREAM, STATUS_GROUP, msg_id)
                    except Exception as e:
                        logger.warning("xack failed: %s", e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("MQ consumer error: %s", e)
            await asyncio.sleep(2)


async def consume_log_stream(redis_client: aioredis.Redis) -> None:
    """Continuously read stp:logs and broadcast to WebSocket."""
    logger.info("MQ log consumer started")
    while True:
        try:
            results = await redis_client.xreadgroup(
                groupname=LOG_GROUP,
                consumername=LOG_CONSUMER,
                streams={LOG_STREAM: ">"},
                count=LOG_BATCH_SIZE,
                block=LOG_BLOCK_MS,
            )
            if not results:
                continue
            for _stream, messages in results:
                for msg_id, fields in messages:
                    await _process_log_message(fields)
                    try:
                        await redis_client.xack(LOG_STREAM, LOG_GROUP, msg_id)
                    except Exception as e:
                        logger.warning("xack log failed: %s", e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("MQ log consumer error: %s", e)
            await asyncio.sleep(2)


async def monitor_backpressure(redis_client: aioredis.Redis) -> None:
    """Periodically check stp:status lag and set/clear backpressure key."""
    logger.info("Backpressure monitor started")
    _last_state: bool = False  # True = backpressure active

    while True:
        try:
            await asyncio.sleep(_BP_CHECK_INTERVAL)
            info = await redis_client.xinfo_groups(STATUS_STREAM)
            lag = 0
            for group in info:
                if group.get("name") == STATUS_GROUP:
                    lag = int(group.get("lag") or group.get("pending", 0))
                    break

            if lag > _LAG_HIGH and not _last_state:
                await redis_client.set(_BP_KEY, "5")
                _last_state = True
                # Notify agents via stp:control
                await redis_client.xadd(
                    "stp:control",
                    {
                        "target_host_id": "*",
                        "command": "backpressure",
                        "log_rate_limit": "5",
                    },
                    maxlen=10_000,
                    approximate=True,
                )
                logger.warning("Backpressure activated (lag=%d)", lag)

            elif lag < _LAG_LOW and _last_state:
                await redis_client.delete(_BP_KEY)
                _last_state = False
                await redis_client.xadd(
                    "stp:control",
                    {
                        "target_host_id": "*",
                        "command": "backpressure",
                        "log_rate_limit": "None",
                    },
                    maxlen=10_000,
                    approximate=True,
                )
                logger.info("Backpressure released (lag=%d)", lag)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("backpressure monitor error: %s", e)


async def _process_status_message(fields: dict) -> None:
    """Persist a single stp:status message to the database."""
    msg_type = fields.get("msg_type", "")
    try:
        if msg_type == "step_trace":
            await _persist_step_trace(fields)
        elif msg_type == "job_status":
            await _persist_job_status(fields)
    except Exception as e:
        logger.warning("process_status_message failed (type=%s): %s", msg_type, e)


async def _process_log_message(fields: dict) -> None:
    """No-op: log broadcasting now handled by SocketIO /agent namespace.

    Kept as a stub so consume_log_stream (still active until Phase 4)
    does not crash on existing messages.  The Redis consumer simply ACKs
    and discards.
    """
    pass


async def _persist_step_trace(fields: dict) -> None:
    from backend.core.database import AsyncSessionLocal
    from backend.services.reconciler import reconcile_step_traces

    trace = {
        "job_id": int(fields.get("job_id", 0)),
        "step_id": fields.get("step_id", ""),
        "stage": fields.get("stage", "execute"),
        "event_type": fields.get("event_type", ""),
        "status": fields.get("status", ""),
        "output": fields.get("output") or None,
        "error_message": fields.get("error_message") or None,
        "original_ts": fields.get("timestamp"),
    }
    if not trace["job_id"] or not trace["step_id"] or not trace["event_type"]:
        return
    async with AsyncSessionLocal() as db:
        await reconcile_step_traces(
            host_id=fields.get("host_id", ""),
            traces=[trace],
            db=db,
        )


async def _persist_job_status(fields: dict) -> None:
    """Compensating path for job status — handles MQ events that arrive before
    or after the primary agent_api.complete_job HTTP call.

    If the job is already in a terminal state, this is a no-op (idempotent).
    Post-completion is only triggered if this consumer is the first to reach
    terminal, preventing duplicate report generation.
    """
    from backend.core.database import AsyncSessionLocal
    from backend.models.enums import JobStatus
    from backend.models.job import JobInstance
    from backend.services.aggregator import WorkflowAggregator
    from backend.services.state_machine import InvalidTransitionError, JobStateMachine
    from backend.realtime.socketio_server import broadcast_run_job_update, broadcast_run_workflow_status
    from backend.services.device_lock import release_lock

    job_id = int(fields.get("job_id", 0))
    status_str = fields.get("status", "").upper()
    reason = fields.get("reason", "mq_compensate")
    if not job_id or not status_str:
        return

    try:
        new_status = JobStatus(status_str)
    except ValueError:
        logger.warning("mq_unknown_job_status: %s", status_str)
        return

    _TERMINAL = {
        JobStatus.COMPLETED.value, JobStatus.FAILED.value,
        JobStatus.ABORTED.value, JobStatus.UNKNOWN.value,
    }

    workflow_run_id = None
    workflow_terminal_status = None
    did_transition = False

    async with AsyncSessionLocal() as db:
        job = await db.get(JobInstance, job_id)
        if job is None:
            return

        # Already terminal — primary path (agent_api) already handled it
        if job.status in _TERMINAL:
            logger.debug(
                "mq_skip_already_terminal job=%d status=%s", job_id, job.status,
            )
            # Still broadcast for WS consistency, but skip DB writes
            workflow_run_id = job.workflow_run_id
            # No commit needed
        else:
            workflow_run_id = job.workflow_run_id
            try:
                JobStateMachine.transition(job, new_status, reason)
                did_transition = True
                if job.status in _TERMINAL:
                    job.ended_at = datetime.utcnow()
                    await WorkflowAggregator.on_job_terminal(job, db)
                    await release_lock(db, job.device_id, job_id)
                    from backend.models.workflow import WorkflowRun
                    run = await db.get(WorkflowRun, workflow_run_id)
                    if run and run.status != "RUNNING":
                        workflow_terminal_status = run.status
                await db.commit()
                logger.info(
                    "mq_compensate_transition job=%d %s->%s",
                    job_id, "RUNNING", new_status.value,
                )
            except InvalidTransitionError:
                logger.debug(
                    "mq_transition_rejected job=%d target=%s current=%s",
                    job_id, new_status.value, job.status,
                )

    # Only trigger post-completion if this consumer actually did the terminal transition
    if did_transition and new_status.value in _TERMINAL:
        try:
            from backend.tasks.saq_worker import get_queue
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
            logger.warning("mq_post_completion_enqueue_failed job=%d: %s", job_id, e)

    if workflow_run_id:
        try:
            await broadcast_run_job_update(workflow_run_id, job_id, new_status.value)
            if workflow_terminal_status:
                await broadcast_run_workflow_status(workflow_run_id, workflow_terminal_status)
        except Exception as e:
            logger.warning("mq_ws_broadcast_failed: %s", e)
