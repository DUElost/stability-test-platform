"""Agent DeviceLogEvent 批量 upsert / 列表（#1520 垂直切片：agent_api DLE）。

``POST /device-log-events``：状态机校验、路径校验、身份不变式、幂等重放、
signal 关联。``GET /device-log-events``：host 待处理恢复拉取。

路由退化为 ``ok(await ingest_agent_device_log_events(...))`` /
``ok(await list_agent_device_log_events(...))``。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_device_event_remote_path,
)
from backend.models.device_log_event import DeviceLogEvent
from backend.models.enums import EventState
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.services.device_log_event import (
    is_unassigned_remote_path,
    resolve_initial_upload_state,
)
from backend.services.errors import BadRequest, Conflict, Forbidden, NotFound

logger = logging.getLogger(__name__)


_VALID_EVENT_STATES = {s.value for s in EventState}

# #1174: 中心 remote_path/checksum 已权威的行（extract 只认这三态，见
# backend/services/device_log_event._REMOTE_STATES）——任何落后补丁（旧 LOCAL
# 注册意图重放、PULL_FAILED 等不带 remote_path 的迟到 patch）不得覆盖降级。
_EXTRACTABLE_STATES = frozenset(
    {EventState.REMOTE.value, EventState.ARCHIVED.value, EventState.PRUNED.value}
)

# #1052（R09-R02）：DLE 状态迁移显式化 —— 合法路径来自 Agent 生命周期
# （LOCAL 注册 → UPLOADING → REMOTE/UPLOAD_FAILED → PRUNED、PULL_FAILED 重试
# 直达 REMOTE）与控制面标记（scan xls 引用 → UPLOAD_PENDING，saq_tasks 直写）。
# 同态重复 = 幂等允许；表外迁移 409 明确拒绝（extractable 降级走上方 #1174
# 幂等忽略分支，不落到本表）。可信边界见
# docs/design/2026-device-log-event-implementation-spec.md §"可信边界"。
#
# 本字面表**不含 PULL_FAILED 出边**——它的语义是「本地源已不可达」，Agent 在本地
# 目录缺失时无条件打它（event_uploader `_upload_one` 的 missing-local 分支），与
# 当时处于哪个在途状态无关，故由下方 _ALLOWED_TRANSITIONS 对所有非终态统一派生。
_TRANSITIONS_LITERAL: dict[str, frozenset] = {
    "DETECTED": frozenset({"LOCAL", "UPLOAD_PENDING", "UPLOADING"}),
    "PULL_FAILED": frozenset({"LOCAL", "UPLOAD_PENDING", "UPLOADING", "REMOTE"}),
    "LOCAL": frozenset({
        "UPLOAD_PENDING", "UPLOADING", "UPLOAD_FAILED", "REMOTE", "PRUNED",
    }),
    "UPLOAD_PENDING": frozenset({
        "LOCAL", "UPLOADING", "UPLOAD_FAILED", "REMOTE", "PRUNED",
    }),
    "UPLOADING": frozenset({"UPLOAD_PENDING", "UPLOAD_FAILED", "REMOTE", "PRUNED"}),
    "UPLOAD_FAILED": frozenset({
        "LOCAL", "UPLOAD_PENDING", "UPLOADING", "REMOTE", "PRUNED",
    }),
    "REMOTE": frozenset({"ARCHIVED", "PRUNED"}),
    "ARCHIVED": frozenset({"PRUNED"}),
    "PRUNED": frozenset(),
}

# #1550：PULL_FAILED 是「源不可达」汇点，对所有**非终态**源都合法。由字面表统一
# 派生而非逐条手写——手写已经漏过一次：UPLOAD_PENDING / UPLOADING 缺这条出边，
# Agent 的 missing-local 补丁吃 409，行永远停在在途态，每 30s（快速恢复）/
# 600s（慢速恢复）重新入队再撞 409，正是 #380 当年要消灭的「UPLOAD_PENDING 永久
# 卡死」。派生式同时保证将来新增状态不会重演。
# 终态（= _EXTRACTABLE_STATES，中心 remote_path/checksum 已权威）不得降级回
# PULL_FAILED，与上方 #1174 的幂等忽略分支同一口径。
_ALLOWED_TRANSITIONS: dict[str, frozenset] = {
    src: (
        allowed
        if src in _EXTRACTABLE_STATES
        else allowed | {EventState.PULL_FAILED.value}
    )
    for src, allowed in _TRANSITIONS_LITERAL.items()
}


class DeviceLogEventIn(BaseModel):
    id: Optional[str] = None
    serial: str
    platform: str
    event_type: str
    event_subtype: Optional[str] = None
    detected_at: str
    device_timestamp: Optional[str] = None
    state: str
    local_path: str
    remote_path: Optional[str] = None
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    plan_run_id: Optional[int] = None
    host_id: str
    job_id: Optional[int] = None
    link_signal_seq_no: Optional[int] = None


class DeviceLogEventBatchIn(BaseModel):
    events: List[DeviceLogEventIn]


def _parse_iso_dt(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadRequest(
            f"device_log_event.{field} invalid ISO8601: {value}",
        ) from exc


def _validated_remote_path(
    raw: Optional[str],
    *,
    plan_run_id: Optional[int] = None,
    event_id: Optional[str] = None,
    unassigned_fallback: bool = False,
) -> Optional[str]:
    if not raw:
        return None
    try:
        return str(resolve_device_event_remote_path(
            raw,
            plan_run_id=plan_run_id,
            event_id=event_id,
            must_exist=False,
        ))
    except ArtifactPathError as exc:
        # #389: 行被 associate 到 plan_run 后，Agent 后续 patch（如 PRUNED）
        # 仍可能带它当初上传的 devices/unassigned/{event_id}/ 旧路径——
        # 该 scope 对本行合法（extract 按 absolute remote_path 解析），
        # 回退接受而不是 400 丢弃状态更新。
        if unassigned_fallback and event_id:
            try:
                return str(resolve_device_event_remote_path(
                    raw,
                    event_id=event_id,
                    must_exist=False,
                ))
            except ArtifactPathError:
                pass
        raise BadRequest(
            f"device_log_event.remote_path invalid: {exc}",
        ) from exc


async def ingest_agent_device_log_events(
    db: AsyncSession,
    payload: DeviceLogEventBatchIn,
) -> dict:
    """批量创建或更新 DeviceLogEvent（ADR-0028 D1）。

    提供 ``id`` 时按主键更新 ``state`` / ``remote_path`` / ``checksum`` 等字段；
    未提供 ``id`` 时插入新行。可选 ``link_signal_seq_no`` 将对应
    ``(job_id, seq_no)`` 的 ``job_log_signal.device_log_event_id`` 关联到本事件。
    """
    if not payload.events:
        return {"upserted": 0, "total": 0}

    upserted = 0
    event_ids: List[str] = []
    now = datetime.now(timezone.utc)

    for ev in payload.events:
        if ev.state not in _VALID_EVENT_STATES:
            raise BadRequest(f"device_log_event.state invalid: {ev.state}")

        detected_dt = _parse_iso_dt(ev.detected_at, "detected_at")
        device_ts = (
            _parse_iso_dt(ev.device_timestamp, "device_timestamp")
            if ev.device_timestamp
            else None
        )

        host = await db.get(Host, ev.host_id)
        if host is None:
            raise NotFound(f"host {ev.host_id} not found")

        if ev.job_id is not None:
            job = await db.get(JobInstance, ev.job_id)
            if job is None:
                raise NotFound(f"job {ev.job_id} not found")
            if job.host_id and job.host_id != ev.host_id:
                raise BadRequest(
                    f"device_log_event.host_id {ev.host_id!r} does not match job host {job.host_id!r}",
                )
            if (
                ev.plan_run_id is not None
                and job.plan_run_id is not None
                and job.plan_run_id != ev.plan_run_id
            ):
                # #1052：host/job/plan_run 三元组必须互相一致——错误组合的
                # 事件会让 extract 在错误的 run 下取数。
                raise BadRequest(
                    f"device_log_event.plan_run_id {ev.plan_run_id} does not "
                    f"match job plan_run {job.plan_run_id}",
                )

        if ev.id:
            try:
                event_id = UUID(ev.id)
            except ValueError as exc:
                raise BadRequest(
                    f"device_log_event.id invalid UUID: {ev.id}",
                ) from exc
            row = await db.get(DeviceLogEvent, event_id)
            if row is None:
                # #1051 / R09-R01: Agent 预分配 id 重试创建 —— 行不存在则插入，
                # 与「无 id 新建」同形，保证同一 idempotency key 可安全重放。
                row = DeviceLogEvent(
                    id=event_id,
                    serial=ev.serial,
                    platform=ev.platform,
                    event_type=ev.event_type,
                    event_subtype=ev.event_subtype,
                    detected_at=detected_dt,
                    device_timestamp=device_ts,
                    # #1956：无 scan 门禁的平台（UNIVIEW）入库即可上送，否则永远停在 LOCAL。
                    state=resolve_initial_upload_state(ev.event_type, ev.state),
                    local_path=ev.local_path,
                    remote_path=_validated_remote_path(
                        ev.remote_path,
                        plan_run_id=ev.plan_run_id,
                        event_id=str(event_id),
                    ),
                    size_bytes=ev.size_bytes,
                    checksum=ev.checksum,
                    plan_run_id=ev.plan_run_id,
                    host_id=ev.host_id,
                    job_id=ev.job_id,
                    signal_seq_no=ev.link_signal_seq_no,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                await db.flush()
            else:
                if row.host_id != ev.host_id:
                    raise Forbidden(
                        f"device_log_event host_id mismatch: "
                        f"{ev.host_id!r} != {row.host_id!r}"
                    )
                # #1052：身份字段不可变——serial / job_id / plan_run_id 与已入库
                # 行不一致视为错误组合（跨设备/跨任务混淆或错误 Agent），403。
                if row.serial != ev.serial:
                    raise Forbidden(
                        f"device_log_event serial mismatch: "
                        f"{ev.serial!r} != {row.serial!r}"
                    )
                if (
                    row.job_id is not None
                    and ev.job_id is not None
                    and row.job_id != ev.job_id
                ):
                    raise Forbidden(
                        f"device_log_event job_id mismatch: "
                        f"{ev.job_id} != {row.job_id}"
                    )
                if (
                    row.plan_run_id is not None
                    and ev.plan_run_id is not None
                    and row.plan_run_id != ev.plan_run_id
                ):
                    raise Forbidden(
                        f"device_log_event plan_run_id mismatch: "
                        f"{ev.plan_run_id} != {row.plan_run_id}"
                    )
                if (
                    row.state in _EXTRACTABLE_STATES
                    and ev.state not in _EXTRACTABLE_STATES
                ):
                    # #1174: 幂等重放/迟到补丁不得把已上送权威副本的行降级。
                    # REMOTE/ARCHIVED/PRUNED 以中心 remote_path/checksum 为准；
                    # 旧 LOCAL 注册意图（无 remote_path）或 PULL_FAILED 等落后
                    # patch 重放到此时覆盖会清空路径并使 extract 不可见——
                    # #1083 REMOTE ack 后本地副本可能已 prune，回退即不可逆。
                    # 按幂等成功处理（客户端据此 ACK 并清掉 outbox 条目）。
                    logger.warning(
                        "dle_stale_replay_ignored id=%s existing=%s incoming=%s",
                        row.id, row.state, ev.state,
                    )
                    event_id = row.id
                else:
                    # #1052：状态迁移显式化（同态幂等；表外 409）。extractable
                    # 降级已在上方 #1174 分支按幂等成功忽略。
                    # #2025：目标态先与两条创建路同口径归一（UNIVIEW 的 LOCAL/
                    # DETECTED 提升为 UPLOAD_PENDING）再做迁移校验与赋值。否则
                    # dle_register_outbox 的同 id state=LOCAL 意图重放会把
                    # UPLOAD_PENDING 打回 LOCAL：该行自此既不进上送队列
                    # （LOCAL = 有意不传），也不计入 count_pending_upload_events
                    # ——事件永久停在 LOCAL，且 merge 门禁看不到它。
                    target_state = resolve_initial_upload_state(ev.event_type, ev.state)
                    if target_state != row.state and target_state not in _ALLOWED_TRANSITIONS.get(
                        row.state, frozenset()
                    ):
                        raise Conflict({
                            "code": "DLE_INVALID_TRANSITION",
                            "message": (
                                "device_log_event state transition not allowed: "
                                f"{row.state} -> {target_state}"
                            ),
                        })
                    row.state = target_state
                    effective_plan_run = (
                        ev.plan_run_id if ev.plan_run_id is not None else row.plan_run_id
                    )
                    incoming_path = _validated_remote_path(
                        ev.remote_path,
                        plan_run_id=effective_plan_run,
                        event_id=str(row.id),
                        unassigned_fallback=True,
                    )
                    # #2316 决定 1：行已归属、且行内已有权威路径时，Agent 带来的
                    # devices/unassigned/ 路径**不覆盖**它——Agent 不知道中心侧的搬移
                    # （方案 C），此后每次补丁（REMOTE/PRUNED/PULL_FAILED）都会带那条
                    # 陈旧路径。400 会把良性陈旧路径变成状态更新失败（Agent outbox
                    # 重试/死信，#380/#764/#1550 同族），故只忽略 + 留痕。
                    # 行内路径为空时仍接受（此时它是唯一信息）。
                    if (
                        row.plan_run_id is not None
                        and row.remote_path
                        and is_unassigned_remote_path(incoming_path)
                    ):
                        logger.info(
                            "dle_unassigned_path_ignored id=%s plan_run=%s incoming=%s",
                            row.id, row.plan_run_id, incoming_path,
                        )
                    else:
                        row.remote_path = incoming_path
                    row.checksum = ev.checksum
                    row.size_bytes = ev.size_bytes
                    # #1052：plan_run_id 只在 payload 显式携带时更新——此前
                    # 无条件赋值会被「不带 plan_run_id 的迟到 patch」清空归属，
                    # extract 作用域随之丢锚。
                    if ev.plan_run_id is not None:
                        row.plan_run_id = ev.plan_run_id
                    row.updated_at = now
                    event_id = row.id
        else:
            # #1051: 无 client id 时，同 job+signal_seq 重放返回已有行（创建幂等）。
            if ev.job_id is not None and ev.link_signal_seq_no is not None:
                existing = (await db.execute(
                    select(DeviceLogEvent).where(
                        DeviceLogEvent.job_id == ev.job_id,
                        DeviceLogEvent.signal_seq_no == ev.link_signal_seq_no,
                    )
                )).scalars().first()
                if existing is not None:
                    event_id = existing.id
                    upserted += 1
                    event_ids.append(str(event_id))
                    continue
            row = DeviceLogEvent(
                serial=ev.serial,
                platform=ev.platform,
                event_type=ev.event_type,
                event_subtype=ev.event_subtype,
                detected_at=detected_dt,
                device_timestamp=device_ts,
                # #1956：无 scan 门禁的平台（UNIVIEW）入库即可上送，否则永远停在 LOCAL。
                state=resolve_initial_upload_state(ev.event_type, ev.state),
                local_path=ev.local_path,
                remote_path=_validated_remote_path(
                    ev.remote_path,
                    plan_run_id=ev.plan_run_id,
                ),
                size_bytes=ev.size_bytes,
                checksum=ev.checksum,
                plan_run_id=ev.plan_run_id,
                host_id=ev.host_id,
                job_id=ev.job_id,
                signal_seq_no=ev.link_signal_seq_no,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            event_id = row.id

        if ev.link_signal_seq_no is not None:
            row.signal_seq_no = ev.link_signal_seq_no

        upserted += 1
        event_ids.append(str(event_id))

    from backend.services.device_log_event import link_signals_to_device_log_events

    await link_signals_to_device_log_events(
        db,
        [ev.job_id for ev in payload.events if ev.job_id is not None],
    )

    await db.commit()
    return {"upserted": upserted, "total": len(payload.events), "event_ids": event_ids}


async def list_agent_device_log_events(
    db: AsyncSession,
    *,
    host_id: str,
    state: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """Agent 重启恢复：按 host + 可选 state 拉取待处理事件（``detected_at`` 升序）。"""
    stmt = select(DeviceLogEvent).where(DeviceLogEvent.host_id == host_id)
    if state:
        states = [s.strip() for s in state.split(",") if s.strip()]
        invalid = [s for s in states if s not in _VALID_EVENT_STATES]
        if invalid:
            raise BadRequest(f"invalid state filter: {invalid}")
        stmt = stmt.where(DeviceLogEvent.state.in_(states))

    stmt = stmt.order_by(DeviceLogEvent.detected_at.asc())
    if limit is not None:
        if limit < 1:
            raise BadRequest("limit must be >= 1")
        stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "events": [
            {
                "id": str(r.id),
                "state": r.state,
                "local_path": r.local_path,
                "remote_path": r.remote_path,
                "serial": r.serial,
                "platform": r.platform,
                "event_type": r.event_type,
                "detected_at": r.detected_at.isoformat(),
                "plan_run_id": r.plan_run_id,
                "job_id": r.job_id,
                "host_id": r.host_id,
            }
            for r in rows
        ],
        "total": len(rows),
    }


# 路由 / 既有测试用的私有名与端点别名。
ingest_device_log_events = ingest_agent_device_log_events
list_device_log_events = list_agent_device_log_events
