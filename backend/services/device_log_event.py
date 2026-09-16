"""DeviceLogEvent query helpers for control-plane SAQ chain (ADR-0028)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

from sqlalchemy import bindparam, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.models.device_log_event import DeviceLogEvent
from backend.models.enums import EventState
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun

logger = logging.getLogger(__name__)


# Upload-complete / extractable: CIFS ``remote_path`` is authoritative.
# Include PRUNED — ``STP_EVENT_UPLOADER_PRUNE_LOCAL`` patches REMOTE→PRUNED
# right after copy (#217); extract must still discover those remote_path dirs.
_REMOTE_STATES = (
    EventState.REMOTE.value,
    EventState.ARCHIVED.value,
    EventState.PRUNED.value,
)

# Clock skew / late upload grace around PlanRun window for unassigned attach (#213 B3).
_ASSOCIATE_GRACE = timedelta(minutes=30)
# #1962：同一宽限也用于「异常仪表盘」的时间窗口。终态 run 的事件可能在 run
# **结束之后**才落库（reconciler 首 tick 的 ls+pull 可能慢于 job 生命周期），
# 原窗口 [started_at, ended_at] 会把本轮自己的事件整片丢弃 —— 表现为
# 「DLE 有行、仪表盘 0」。这里公开同一常量，避免两处又各写一个宽限值。
LATE_EVENT_GRACE = _ASSOCIATE_GRACE

# #1956：无 scan 门禁的平台事件类型。
#
# MTK（AEE 家族）的上送由控制面 ``upload_task`` 依 **scan xls 引用**标记 UPLOAD_PENDING
# 触发；展锐（UNIVIEW）**没有 scan 产物**，于是其事件永远停在 LOCAL——
# 而 LOCAL 在本模型的语义是「有 scan 但未被引用 → 有意不传」（见上方
# ``count_pending_upload_events`` 注释），属语义误用。
#
# 展锐事件的「有效性」由 Agent 侧解析期判定（#1946：normalboot-only 目录直接丢弃），
# 因此入库即可上送；这与 ``saq_tasks`` 里「barrier 需等 UNISOC uploads land」的
# 既有预期一致。
_NO_SCAN_GATE_EVENT_TYPES = frozenset({"UNIVIEW"})
# 尚未进入上送流程的「等待」态：只有它们才允许被提升为 UPLOAD_PENDING。
_AWAITING_UPLOAD_STATES = frozenset({"DETECTED", "LOCAL"})


def resolve_initial_upload_state(event_type: str, requested_state: str) -> str:
    """决定事件入库时的初始状态（#1956）。

    无 scan 门禁的平台（UNIVIEW）在不处于上送流程的等待态时，直接提升为
    ``UPLOAD_PENDING``，由 Agent 的 EventUploader 上送；其余情况原样返回，
    保持 MTK 的「scan 引用后才传」语义不变。
    """
    normalized_type = str(event_type or "").strip().upper()
    normalized_state = str(requested_state or "").strip().upper()
    if (
        normalized_type in _NO_SCAN_GATE_EVENT_TYPES
        and normalized_state in _AWAITING_UPLOAD_STATES
    ):
        return "UPLOAD_PENDING"
    return requested_state


def count_pending_upload_events(db: Session, plan_run_id: int) -> int:
    """plan_run 下尚未完成上送的事件数。

    ADR-0028 方案 A 过滤模型（#287：CONTINUOUS 全量分支已删除）：仅计数
    UPLOAD_PENDING / UPLOADING / UPLOAD_FAILED；LOCAL 是「有意不传」
    （未被 scan xls 引用），不阻塞 merge。
    """
    _IN_FLIGHT = {"UPLOAD_PENDING", "UPLOADING", "UPLOAD_FAILED"}
    return int(
        db.execute(
            select(func.count(DeviceLogEvent.id)).where(
                DeviceLogEvent.plan_run_id == plan_run_id,
                DeviceLogEvent.state.in_(_IN_FLIGHT),
            )
        ).scalar()
        or 0
    )


def count_remote_events(
    db: Session,
    plan_run_id: int,
    *,
    host_ids: Sequence[str] | None = None,
    since: datetime | None = None,
) -> int:
    stmt = select(func.count(func.distinct(DeviceLogEvent.host_id))).where(
        DeviceLogEvent.plan_run_id == plan_run_id,
        DeviceLogEvent.state.in_(_REMOTE_STATES),
    )
    if host_ids:
        stmt = stmt.where(DeviceLogEvent.host_id.in_(list(host_ids)))
    if since is not None:
        stmt = stmt.where(DeviceLogEvent.updated_at >= since)
    return int(db.execute(stmt).scalar() or 0)


def summarize_upload_states(db: Session, plan_run_id: int) -> dict[str, int]:
    """按 state 分组统计 plan_run 的事件上送进度（→ run_context.upload_summary）。

    与 count_pending_upload_events / count_remote_events 同源：
    ``pending`` = UPLOAD_PENDING + UPLOADING + UPLOAD_FAILED；
    ``remote`` = REMOTE + ARCHIVED + PRUNED（可提取/已完成）；
    LOCAL 是过滤模型下「有意不传」的事件，单独展示便于判断缺口。
    """
    rows = db.execute(
        select(DeviceLogEvent.state, func.count(DeviceLogEvent.id)).where(
            DeviceLogEvent.plan_run_id == plan_run_id,
        ).group_by(DeviceLogEvent.state)
    ).all()
    by_state = {state: 0 for state in EventState}
    total = 0
    for state, n in rows:
        try:
            by_state[EventState(state)] = n
        except ValueError:
            continue
        total += n
    return {
        "total": total,
        "detected": by_state[EventState.DETECTED],
        "pull_failed": by_state[EventState.PULL_FAILED],
        "local": by_state[EventState.LOCAL],
        "upload_pending": by_state[EventState.UPLOAD_PENDING],
        "uploading": by_state[EventState.UPLOADING],
        "upload_failed": by_state[EventState.UPLOAD_FAILED],
        "failed": by_state[EventState.UPLOAD_FAILED],
        "archived": by_state[EventState.ARCHIVED],
        "pruned": by_state[EventState.PRUNED],
        "pending": (
            by_state[EventState.UPLOAD_PENDING]
            + by_state[EventState.UPLOADING]
            + by_state[EventState.UPLOAD_FAILED]
        ),
        "remote": (
            by_state[EventState.REMOTE]
            + by_state[EventState.ARCHIVED]
            + by_state[EventState.PRUNED]
        ),
    }


def list_remote_paths_for_extract(db: Session, plan_run_id: int) -> list[str]:
    rows = db.execute(
        select(DeviceLogEvent.remote_path).where(
            DeviceLogEvent.plan_run_id == plan_run_id,
            DeviceLogEvent.state.in_(_REMOTE_STATES),
            DeviceLogEvent.remote_path.isnot(None),
        )
    ).scalars().all()
    return [str(p) for p in rows if p]


def associate_unassigned_events_to_plan_run(db: Session, plan_run_id: int) -> int:
    """Attach ``plan_run_id IS NULL`` events to this PlanRun (#213 B3).

    Two paths (OR):
    1. ``job_id`` belongs to a JobInstance of this PlanRun (strong).
    2. ``job_id IS NULL`` and ``serial`` ∈ PlanRun devices and ``detected_at``
       within ``[started_at - grace, (ended_at or now) + grace]``.
       Serial fallback must not steal events whose ``job_id`` belongs to
       another PlanRun (#230 review).

    Does not move NFS paths; ``remote_path`` may stay under
    ``devices/unassigned/{event_id}/`` — extract still copies via that path.
    搬移由 :func:`adopt_unassigned_event_dirs` 在 extract 前单独完成（#2316 方案 C：
    只搬已上送完的 extractable 态；在途态与历史残留仍由 #2262 方案 A 兜底）。
    """
    from backend.services.plan_run_scan_scope import load_plan_run_device_serials

    plan_run = db.get(PlanRun, plan_run_id)
    if plan_run is None:
        return 0

    now = datetime.now(timezone.utc)
    started = plan_run.started_at or now
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    ended = plan_run.ended_at or now
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    window_start = started - _ASSOCIATE_GRACE
    window_end = ended + _ASSOCIATE_GRACE

    serials = load_plan_run_device_serials(db, plan_run_id)
    job_ids = db.execute(
        select(JobInstance.id).where(JobInstance.plan_run_id == plan_run_id)
    ).scalars().all()
    job_ids = [int(j) for j in job_ids]

    clauses = []
    if job_ids:
        clauses.append(DeviceLogEvent.job_id.in_(job_ids))
    if serials:
        clauses.append(
            DeviceLogEvent.job_id.is_(None)
            & (DeviceLogEvent.serial.in_(serials))
            & (DeviceLogEvent.detected_at >= window_start)
            & (DeviceLogEvent.detected_at <= window_end)
        )
    if not clauses:
        return 0

    result = db.execute(
        update(DeviceLogEvent)
        .where(
            DeviceLogEvent.plan_run_id.is_(None),
            or_(*clauses),
        )
        .values(plan_run_id=plan_run_id, updated_at=now)
    )
    db.commit()
    n = int(result.rowcount or 0)
    if n:
        logger.info(
            "device_log_event_associated plan_run=%d count=%d",
            plan_run_id, n,
        )
    return n


def is_unassigned_remote_path(raw: Optional[str]) -> bool:
    """路径是否落在 ``devices/unassigned/`` 下（#2316 决定 1 的判据用）。

    与 retention 侧的 ``remote_path LIKE '%/devices/unassigned/%'`` 同口径：先按
    字符串判形态，再由各自的容器化校验把关（此处只用于「要不要忽略这条补丁」）。
    """
    return bool(raw) and "/devices/unassigned/" in str(raw)


def adopt_unassigned_event_dirs(db: Session, plan_run_id: int) -> int:
    """#2316（方案 C）：把本 run 已归属、且 ``remote_path`` 仍在
    ``devices/unassigned/{event_id}/`` 的事件目录**搬进**
    ``devices/{plan_run_id}/{event_id}/``，此后由 run 级 purge 统一回收。

    **只搬 extractable 态**（``_REMOTE_STATES``：REMOTE / ARCHIVED / PRUNED）：这些
    事件的副本已上送完成，Agent 不会再往源目录写；在途态搬移会与 Agent 的写入分叉
    （它仍按自己算出的 unassigned 路径写），留给 #2262 方案 A（行删时清目录）兜底。

    **每次 extract 前都跑**（而非只在关联那一刻）：行可能先关联、后才变 REMOTE
    （Agent 末次补丁晚到），那一刻没有别人会再搬它。

    幂等/可续——DB 提交与文件系统 rename 无法原子，故以**盘上实况**为准：
    目标在、源不在 → 只补行更新（上次崩在 rename 与 commit 之间）；源在、目标不在
    → rename 后更新行；两者都在 → 跳过并告警（不猜哪个权威）；都不在 → 跳过
    （extract 侧会记 missing）。rename 失败一律**不动行**（保持 unassigned 路径，
    extract 照常可读——fail-safe，绝不做跨设备拷贝式的半搬运）。

    返回本次完成归属（含仅补行更新）的事件数。
    """
    from backend.core.storage_root import resolve_shared_storage_root

    root = resolve_shared_storage_root()
    if not root:
        logger.warning("adopt_unassigned_skipped_root_unset plan_run=%d", plan_run_id)
        return 0

    rows = db.execute(
        select(DeviceLogEvent.id, DeviceLogEvent.remote_path).where(
            DeviceLogEvent.plan_run_id == plan_run_id,
            DeviceLogEvent.state.in_(_REMOTE_STATES),
            DeviceLogEvent.remote_path.like("%/devices/unassigned/%"),
        )
    ).all()
    if not rows:
        return 0

    devices_root = (Path(root) / "devices").resolve()
    unassigned_root = devices_root / "unassigned"
    run_root = devices_root / str(int(plan_run_id))
    adopted = 0
    for event_id, raw_path in rows:
        # 事件目录 = ``unassigned/{event_id}/``，而 remote_path 指向**它内部的一层**
        # （``{event_id}/{basename}``，见 event_uploader 的 dst 拼装；生产实测即此形态）。
        # 用行 id 定位事件目录是确定的，再用「remote_path 必须落在该目录内」把两者绑定。
        src_event_dir = unassigned_root / str(event_id)
        dst_event_dir = run_root / str(event_id)
        try:
            p = Path(str(raw_path)).resolve()
        except OSError:
            p = None
        if p is None or not p.is_relative_to(src_event_dir):
            # 形态不符（含借 '..' 越界）：不搬，等方案 A/人工处理。
            logger.warning(
                "adopt_unassigned_path_invalid plan_run=%d path=%s",
                plan_run_id, raw_path,
            )
            continue
        tail = p.relative_to(src_event_dir)
        try:
            if dst_event_dir.exists() and not src_event_dir.exists():
                pass  # 上次崩在 rename 与 commit 之间：只补行更新
            elif src_event_dir.exists() and not dst_event_dir.exists():
                dst_event_dir.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src_event_dir, dst_event_dir)
            elif src_event_dir.exists() and dst_event_dir.exists():
                logger.warning(
                    "adopt_unassigned_both_present plan_run=%d src=%s dst=%s — skipped",
                    plan_run_id, src_event_dir, dst_event_dir,
                )
                continue
            else:
                continue  # 盘上都不在：extract 侧记 missing
        except OSError:
            logger.warning(
                "adopt_unassigned_rename_failed plan_run=%d path=%s — 保持 unassigned 路径",
                plan_run_id, raw_path, exc_info=True,
            )
            continue
        db.execute(
            update(DeviceLogEvent)
            .where(DeviceLogEvent.id == event_id)
            .values(
                remote_path=str(dst_event_dir / tail),
                updated_at=datetime.now(timezone.utc),
            )
        )
        adopted += 1
    if adopted:
        db.commit()
        logger.info("adopt_unassigned_done plan_run=%d events=%d", plan_run_id, adopted)
    return adopted


def mark_events_archived(
    db: Session,
    plan_run_id: int,
    remote_paths: Sequence[str],
) -> int:
    """extract 成功后把仍为 REMOTE 的事件标为 ARCHIVED。

    Already-``PRUNED`` rows (local deleted after upload) keep ``PRUNED``;
    their ``remote_path`` remains extractable via ``list_remote_paths_for_extract``.
    """
    paths = [str(p) for p in remote_paths if p]
    if not paths:
        return 0
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(DeviceLogEvent)
        .where(
            DeviceLogEvent.plan_run_id == plan_run_id,
            DeviceLogEvent.state == EventState.REMOTE.value,
            DeviceLogEvent.remote_path.in_(paths),
        )
        .values(state=EventState.ARCHIVED.value, updated_at=now)
    )
    db.commit()
    return int(result.rowcount or 0)


_LINK_SIGNAL_SQL = text(
    """
    UPDATE job_log_signal AS s
       SET device_log_event_id = e.id
      FROM device_log_event AS e
     WHERE s.job_id = e.job_id
       AND s.seq_no = e.signal_seq_no
       AND s.device_log_event_id IS NULL
       AND e.signal_seq_no IS NOT NULL
       AND e.job_id IN :job_ids
    """
).bindparams(bindparam("job_ids", expanding=True))


def link_signals_to_device_log_events_sync(
    db: Session,
    job_ids: Sequence[int],
) -> int:
    """Link signals when the DLE landed first (#214 / #528).

    Callers own the transaction: signal ingest (``agent_api``) and the
    ``signal_link_reconcile`` sweep (#556). Do not call from a request path
    whose session is never committed — the UPDATE would silently roll back.
    """
    ids = sorted({int(jid) for jid in job_ids if jid is not None})
    if not ids:
        return 0
    result = db.execute(_LINK_SIGNAL_SQL, {"job_ids": ids})
    return int(result.rowcount or 0)


async def link_signals_to_device_log_events(
    db: AsyncSession,
    job_ids: Sequence[int],
) -> None:
    """Attach job_log_signal.device_log_event_id when DLE landed first (#214)."""
    ids = sorted({int(jid) for jid in job_ids if jid is not None})
    if not ids:
        return
    await db.execute(_LINK_SIGNAL_SQL, {"job_ids": ids})


def list_plan_run_device_log_events(
    db: Session,
    plan_run_id: int,
    *,
    skip: int = 0,
    limit: int = 200,
    state: str | None = None,
    platform: str | None = None,
) -> tuple[list[DeviceLogEvent], int]:
    """PlanRun-scoped DLE rows for terminal archive views (#529, #2184).

    ``platform`` 过滤在**服务端**做（与 ``state`` 同构）：客户端过滤只会作用于已加载页，
    在分页场景下会给出「MTK 只有 3 条」这类错误印象（实际是第 1 页只有 3 条）。
    """
    filters = [DeviceLogEvent.plan_run_id == plan_run_id]
    if state:
        filters.append(DeviceLogEvent.state == state)
    if platform:
        filters.append(DeviceLogEvent.platform == platform)
    total = int(
        db.execute(
            select(func.count(DeviceLogEvent.id)).where(*filters)
        ).scalar()
        or 0
    )
    rows = db.execute(
        select(DeviceLogEvent)
        .where(*filters)
        .order_by(DeviceLogEvent.detected_at.desc(), DeviceLogEvent.id)
        .offset(max(0, skip))
        .limit(max(1, min(limit, 500)))
    ).scalars().all()
    return list(rows), total
