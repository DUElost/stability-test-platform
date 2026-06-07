"""State reconciler: idempotent StepTrace upsert from Agent replay."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import StepTrace

logger = logging.getLogger(__name__)


async def reconcile_step_traces(
    host_id: str,
    traces: List[dict],
    db: AsyncSession,
) -> dict:
    """
    Idempotently insert StepTraces from Agent replay.

    Returns ``{"inserted": int, "transitioned_jobs": list[int]}``.
    Unique constraint (job_id, step_id, event_type) prevents duplicates.

    StepTrace replay is a timeline durability path only. Job terminal state is
    owned by ``POST /agent/jobs/{job_id}/complete`` and its terminal outbox.
    Do not advance the Job state machine from ordinary StepTrace replay.
    """
    inserted = 0

    for t in traces:
        stmt = (
            pg_insert(StepTrace)
            .values(
                job_id=t["job_id"],
                step_id=t["step_id"],
                stage=t.get("stage", "execute"),
                event_type=t["event_type"],
                status=t.get("status", ""),
                output=t.get("output"),
                error_message=t.get("error_message"),
                original_ts=_parse_ts(t.get("original_ts")),
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(
                constraint="uq_step_trace_idempotent"
            )
        )
        result = await db.execute(stmt)
        if result.rowcount > 0:
            inserted += 1

    await db.commit()
    logger.info(
        "reconcile: host=%s inserted=%d/%d transitioned=%d",
        host_id, inserted, len(traces), 0,
    )
    return {"inserted": inserted, "transitioned_jobs": []}


def _parse_ts(ts_str: str | None) -> datetime:
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
