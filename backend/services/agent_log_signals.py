"""Agent LogSignal 批量摄取（#1520 垂直切片：agent_api /log-signals）。

``POST /log-signals``：契约校验 → upload lease fencing → 部分接受拒绝清单 →
PG ON CONFLICT 幂等入库 → log_signal_count 累加 → DLE 关联 → watcher 广播。

``require_job_bound_upload_lease`` 亦供 artifacts 路由经 re-export 使用。
路由退化为 ``ok(await ingest_agent_log_signals(...))``。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.metrics import record_log_signal_ingested
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseType
from backend.models.host import Device
from backend.models.job import JobInstance, JobLogSignal

logger = logging.getLogger(__name__)

TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}

# #2792：列宽从模型列定义派生（String 才有 length，Text/JSONB/数值列无长度不收），
# 守卫与 schema 不会漂移。agent 侧契约（watcher contracts）只校验取值域不校验长度，
# 单条超宽会让整条多行 INSERT 被 PG abort（value too long）——#1048 的批毒化形态，
# 故入库前逐条折入 rejected。
def _log_signal_column_widths() -> Dict[str, int]:
    return {
        c.key: int(c.type.length)
        for c in JobLogSignal.__table__.columns
        if getattr(c.type, "length", None)
    }


def log_signal_column_overflow(row: Dict[str, Any]) -> List[str]:
    """返回该行超出列宽的字段名清单（空 = 无溢出）。"""
    widths = _log_signal_column_widths()
    return sorted(
        key for key, width in widths.items()
        if row.get(key) is not None and len(str(row[key])) > width
    )


class LogSignalIn(BaseModel):
    """单条 log_signal 信封。

    字段契约见 backend/agent/watcher/contracts.py LogSignalEnvelope。
    幂等键：(job_id, seq_no)
    """
    job_id:         int
    fencing_token:  str
    agent_instance_id: str
    seq_no:         int
    host_id:        str
    device_serial:  str
    category:       str
    source:         str
    path_on_device: str
    detected_at:    str
    artifact_uri:   Optional[str] = None
    sha256:         Optional[str] = None
    size_bytes:     Optional[int] = None
    first_lines:    Optional[str] = None
    extra:          Optional[Dict[str, Any]] = None


class LogSignalBatchIn(BaseModel):
    signals: List[LogSignalIn]


async def require_job_bound_upload_lease(
    db: AsyncSession,
    job: JobInstance,
    *,
    fencing_token: str,
    agent_instance_id: str,
    host_id: str,
    device_serial: str,
) -> DeviceLease:
    """Authorize active and delayed terminal uploads by historical token."""
    lease = (await db.execute(
        select(DeviceLease)
        .where(
            DeviceLease.job_id == job.id,
            DeviceLease.device_id == job.device_id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.fencing_token == fencing_token,
        )
        .order_by(DeviceLease.id.desc())
    )).scalars().first()
    device = await db.get(Device, job.device_id)
    if (
        lease is None
        or device is None
        or lease.host_id != host_id
        or job.host_id != host_id
        or (
            job.status not in TERMINAL_JOB_STATUSES
            and device.host_id != host_id
        )
        or lease.agent_instance_id != agent_instance_id
        or (device.serial or "").strip() != (device_serial or "").strip()
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "UPLOAD_FENCING_MISMATCH"},
        )
    return lease


async def ingest_agent_log_signals(
    db: AsyncSession,
    payload: LogSignalBatchIn,
) -> dict:
    """批量摄取 Agent watcher 采集的异常信号。

    幂等：用 PostgreSQL `ON CONFLICT (job_id, seq_no) DO NOTHING` 去重。
    副作用：按本批实际新插入数累加 job_instance.log_signal_count。
    契约：字段校验见 backend.agent.watcher.contracts.validate_log_signal
    部分接受（#1048）：单条**永久**不可恢复（契约违规 / job 不存在 / 租约
    fencing 不匹配 / detected_at 非法）只隔离该条并在响应 ``rejected`` 里逐条
    报告，不再整批 404/400 连坐 —— 否则 50 条批次混入一条坏记录，其余正常信号
    会被 Agent 侧反复重试直至全部进死信。暂时性失败（DB 不可用等）仍整批失败。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from backend.agent.watcher.contracts import ContractViolation, validate_log_signal

    if not payload.signals:
        return {"inserted": 0, "total": 0}

    def _rejected_item(s: LogSignalIn, reason: str) -> Dict[str, Any]:
        return {
            "job_id": s.job_id,
            "seq_no": s.seq_no,
            "reason": reason[:300],
        }

    rejected: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    for s in payload.signals:
        envelope = s.model_dump()
        try:
            validate_log_signal(envelope)
        except ContractViolation as exc:
            rejected.append(_rejected_item(s, f"log_signal contract violation: {exc}"))
            continue
        job = await db.get(JobInstance, s.job_id)
        if job is None:
            rejected.append(_rejected_item(s, f"job {s.job_id} not found"))
            continue
        try:
            await require_job_bound_upload_lease(
                db,
                job,
                fencing_token=s.fencing_token,
                agent_instance_id=s.agent_instance_id,
                host_id=s.host_id,
                device_serial=s.device_serial,
            )
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                detail = detail.get("code") or str(detail)
            rejected.append(_rejected_item(
                s, f"lease_check_failed({exc.status_code}): {detail}",
            ))
            continue

        # detected_at: ISO string → datetime
        try:
            detected_dt = datetime.fromisoformat(s.detected_at.replace("Z", "+00:00"))
        except ValueError:
            rejected.append(_rejected_item(
                s, f"log_signal.detected_at invalid ISO8601: {s.detected_at}",
            ))
            continue

        rows.append({
            "job_id":         s.job_id,
            "host_id":        s.host_id,
            "device_serial":  s.device_serial,
            "seq_no":         s.seq_no,
            "category":       s.category,
            "source":         s.source,
            "path_on_device": s.path_on_device,
            "artifact_uri":   s.artifact_uri,
            "sha256":         s.sha256,
            "size_bytes":     s.size_bytes,
            "first_lines":    s.first_lines,
            "detected_at":    detected_dt,
            "extra":          s.extra,
        })

    # #2792：超宽行逐条拒绝——单条超宽会让 PG abort 整条多行 INSERT，
    # 同批其余信号一起进死信（#1048 批毒化）。列宽从模型派生（见模块头）。
    kept: List[Dict[str, Any]] = []
    for row in rows:
        over = log_signal_column_overflow(row)
        if over:
            rejected.append({
                "job_id": row["job_id"],
                "seq_no": row["seq_no"],
                "reason": f"log_signal field exceeds column width: {over}"[:300],
            })
        else:
            kept.append(row)
    rows = kept

    # #1048：整批被拒（无一条可入库）→ 直接返回逐条拒绝清单
    if not rows:
        return {
            "inserted": 0,
            "total": len(payload.signals),
            "rejected": rejected,
        }

    # PostgreSQL 幂等 upsert：ON CONFLICT (job_id, seq_no) DO NOTHING
    stmt = pg_insert(JobLogSignal).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["job_id", "seq_no"])
    # RETURNING id + (job_id, seq_no, category) 以统计实际新增条数 + Prometheus 分类
    stmt = stmt.returning(
        JobLogSignal.id,
        JobLogSignal.job_id,
        JobLogSignal.seq_no,
        JobLogSignal.category,
    )
    result = await db.execute(stmt)
    inserted_rows = result.all()

    # 按 job 分组累加 log_signal_count
    inserted_count_by_job: Dict[int, int] = {}
    for row in inserted_rows:
        inserted_count_by_job[row.job_id] = inserted_count_by_job.get(row.job_id, 0) + 1

    for jid, count in inserted_count_by_job.items():
        await db.execute(
            JobInstance.__table__.update()
            .where(JobInstance.id == jid)
            .values(log_signal_count=JobInstance.log_signal_count + count)
        )

    from backend.services.device_log_event import link_signals_to_device_log_events

    await link_signals_to_device_log_events(db, [row["job_id"] for row in rows])

    await db.commit()

    # ── Prometheus 埋点:仅对实际入库的 signal 计数(冲突丢弃的不计) ──
    for row in inserted_rows:
        record_log_signal_ingested(row.category)

    # ── ADR-0021 C5c: 推 watcher_signal 增量到 plan_run room ──
    # 事件作为 invalidation hint 使用,前端收到后 refetch /watcher-summary。
    # 失败不影响入库结果(socket 服务未起 / 未连前端时静默)。
    if inserted_rows:
        try:
            from backend.realtime.socketio_server import broadcast_watcher_signal

            job_ids = list({row.job_id for row in inserted_rows})
            run_map_rows = (
                (
                    await db.execute(
                        select(JobInstance.id, JobInstance.plan_run_id)
                        .where(JobInstance.id.in_(job_ids))
                    )
                ).all()
            )
            run_id_by_job: Dict[int, int] = {
                jid: rid for jid, rid in run_map_rows if rid is not None
            }
            # Build a lookup from the original payload for device_serial enrichment.
            serial_by_seq: Dict[tuple, Optional[str]] = {
                (s.job_id, s.seq_no): s.device_serial for s in payload.signals
            }
            for row in inserted_rows:
                run_id = run_id_by_job.get(row.job_id)
                if run_id is None:
                    continue
                await broadcast_watcher_signal(
                    run_id,
                    job_id=row.job_id,
                    device_serial=serial_by_seq.get((row.job_id, row.seq_no)),
                    category=row.category,
                    inserted_count=1,
                )
        except Exception:
            logger.debug("broadcast_watcher_signal_failed", exc_info=True)

    return {
        "inserted": len(inserted_rows),
        "total": len(payload.signals),
        "rejected": rejected,
    }


# 路由 / 既有测试用的私有名与端点别名。
_require_job_bound_upload_lease = require_job_bound_upload_lease
_TERMINAL = TERMINAL_JOB_STATUSES
ingest_log_signals = ingest_agent_log_signals
