"""G1 事实底座的服务层入口（ADR-0056 D1/D2，ADR-0062，B8 G1）。

后续 G2–G8 只经本模块读写 ``issue_signature`` / ``issue_occurrence`` /
``issue_occurrence_event`` / ``partition_mode`` / ``scan_round`` /
``representative_selection`` / ``delivery_version`` / ``spill_ledger``，
不得直接写表。

约束由本层强制执行：

- 事实行只追加、不改写；重复写入按业务唯一键收敛为同一行。
- ``partition_mode`` 写入后不可改（D13 第 2 条）。
- ``representative_selection`` 进入「已选」后 ``dle_id`` 不可改（D7 第 6 条）。
- 出现次数与机器数是查询结果，不保存可累加的计数列（I1）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Sequence

from sqlalchemy import String, and_, cast, distinct, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from backend.models.issue_fact import (
    DeliveryVersion,
    EvidencePublication,
    IssueOccurrence,
    IssueOccurrenceEvent,
    IssueSignature,
    PartitionMode,
    RepresentativeSelection,
    ScanRound,
    SpillLedger,
)
from backend.models.device_log_event import DeviceLogEvent
from backend.models.plan_run import PlanRun


# 运行模式（ADR-0062 D13）。
PARTITION_MODE_CURRENT = "CURRENT"
PARTITION_MODE_DEDUP_FIRST = "DEDUP_FIRST"
_PARTITION_MODES = frozenset({PARTITION_MODE_CURRENT, PARTITION_MODE_DEDUP_FIRST})

# 轮次种类与状态（ADR-0062 D3 第 2 条）。
SCAN_ROUND_KIND_PERIODIC = "PERIODIC"
SCAN_ROUND_KIND_FINAL = "FINAL"
SCAN_ROUND_KIND_SUPPLEMENT = "SUPPLEMENT"
_SCAN_ROUND_KINDS = frozenset({
    SCAN_ROUND_KIND_PERIODIC,
    SCAN_ROUND_KIND_FINAL,
    SCAN_ROUND_KIND_SUPPLEMENT,
})
SCAN_ROUND_STATUS_PENDING = "PENDING"
SCAN_ROUND_STATUS_RUNNING = "RUNNING"
SCAN_ROUND_STATUS_CLOSED = "CLOSED"
SCAN_ROUND_MERGE_TERMINAL = frozenset({"OK", "NO_INPUT", "FAILED_FINAL"})
_SCAN_ROUND_STATUSES = frozenset({
    SCAN_ROUND_STATUS_PENDING,
    SCAN_ROUND_STATUS_RUNNING,
    SCAN_ROUND_STATUS_CLOSED,
})

# 选择状态与证据完整度（ADR-0062 D7 第 2 条）。
SELECTION_PENDING = "PENDING"
SELECTION_SELECTED = "SELECTED"
_SELECTION_STATUSES = frozenset({SELECTION_PENDING, SELECTION_SELECTED})
EVIDENCE_COMPLETE = "COMPLETE"
EVIDENCE_INCOMPLETE = "INCOMPLETE"
_EVIDENCE_STATUSES = frozenset({EVIDENCE_COMPLETE, EVIDENCE_INCOMPLETE})

# 首见来源（local / spill / baseline）。
FIRST_SEEN_LOCAL = "local"
FIRST_SEEN_SPILL = "spill"
FIRST_SEEN_BASELINE = "baseline"
_FIRST_SEEN_SOURCES = frozenset({
    FIRST_SEEN_LOCAL,
    FIRST_SEEN_SPILL,
    FIRST_SEEN_BASELINE,
})


class IssueFactError(Exception):
    """事实底座服务层的基类异常。"""


class ImmutableIssueFactError(IssueFactError):
    """尝试改写已写入且不可变的事实（模式 / 已选代表）。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_one_of(value: str, allowed: frozenset, field: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field} invalid: {value!r}")
    return value


def record_issue_signature(
    db: Session,
    *,
    signature_raw: str,
    normalized_key: str,
    platform: str,
    event_type: str,
    seen_at: datetime,
) -> IssueSignature:
    """按 ``(platform, event_type, normalized_key)`` 幂等登记签名。

    已存在时只把 ``last_seen_at`` 推到更晚的观测时间；首次原文与首次时间不改。
    """
    stmt = select(IssueSignature).where(
        IssueSignature.platform == platform,
        IssueSignature.event_type == event_type,
        IssueSignature.normalized_key == normalized_key,
    )
    signature = db.execute(stmt).scalars().first()
    if signature is None:
        signature = IssueSignature(
            signature_raw=signature_raw,
            normalized_key=normalized_key,
            platform=platform,
            event_type=event_type,
            issue_keys=[],
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
        db.add(signature)
        db.flush()
        return signature

    if seen_at > signature.last_seen_at:
        signature.last_seen_at = seen_at
        db.flush()
    return signature


def record_issue_occurrence(
    db: Session,
    *,
    signature_id: int,
    serial: str,
    observed_at: datetime,
    first_seen_source: str,
    occurrence_key: Optional[str] = None,
    build_version: Optional[str] = None,
    project_key: Optional[str] = None,
    specialty_key: Optional[str] = None,
    risk_level: Optional[str] = None,
    first_seen_plan_run_id: Optional[int] = None,
    first_seen_round_id: Optional[int] = None,
) -> IssueOccurrence:
    """按 ``occurrence_key`` 幂等登记发生（I1）。

    同一发生被不同 job 的 baseline 重扫或经溢出副本再次观察时收敛为同一行。
    ``occurrence_key`` 为空表示身份不可证明，按 fail-open 每次新建一行。
    """
    _require_one_of(first_seen_source, _FIRST_SEEN_SOURCES, "first_seen_source")

    if occurrence_key:
        existing = db.execute(
            select(IssueOccurrence).where(
                IssueOccurrence.occurrence_key == occurrence_key,
            )
        ).scalars().first()
        if existing is not None:
            if existing.signature_id != signature_id:
                raise ImmutableIssueFactError(
                    "issue_occurrence signature_id mismatch: "
                    f"{signature_id} != {existing.signature_id} "
                    f"(occurrence_key={occurrence_key})",
                )
            return existing

    occurrence = IssueOccurrence(
        signature_id=signature_id,
        occurrence_key=occurrence_key,
        serial=serial,
        build_version=build_version,
        project_key=project_key,
        specialty_key=specialty_key,
        risk_level=risk_level,
        observed_at=observed_at,
        first_seen_source=first_seen_source,
        first_seen_plan_run_id=first_seen_plan_run_id,
        first_seen_round_id=first_seen_round_id,
    )
    db.add(occurrence)
    db.flush()
    return occurrence


def link_occurrence_event(
    db: Session,
    *,
    occurrence_id: int,
    dle_id: Any,
    signature_id: int,
) -> IssueOccurrenceEvent:
    """登记发生 ↔ DLE 观察的映射；``(occurrence_id, dle_id, signature_id)`` 幂等。"""
    existing = db.execute(
        select(IssueOccurrenceEvent).where(
            IssueOccurrenceEvent.occurrence_id == occurrence_id,
            IssueOccurrenceEvent.dle_id == dle_id,
            IssueOccurrenceEvent.signature_id == signature_id,
        )
    ).scalars().first()
    if existing is not None:
        return existing

    link = IssueOccurrenceEvent(
        occurrence_id=occurrence_id,
        dle_id=dle_id,
        signature_id=signature_id,
    )
    db.add(link)
    db.flush()
    return link


def record_partition_mode(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    mode: str,
    written_at: Optional[datetime] = None,
) -> PartitionMode:
    """写入分区模式；同一分区再次写入不同模式时拒绝（D13 第 2 条）。"""
    _require_one_of(mode, _PARTITION_MODES, "mode")

    existing = db.execute(
        select(PartitionMode).where(
            PartitionMode.plan_run_id == plan_run_id,
            PartitionMode.platform == platform,
        )
    ).scalars().first()
    if existing is not None:
        if existing.mode != mode:
            raise ImmutableIssueFactError(
                "partition_mode is immutable: "
                f"plan_run_id={plan_run_id} platform={platform} "
                f"existing={existing.mode} incoming={mode}",
            )
        return existing

    row = PartitionMode(
        plan_run_id=plan_run_id,
        platform=platform,
        mode=mode,
        written_at=written_at or _utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def get_partition_mode(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
) -> Optional[PartitionMode]:
    """返回分区模式记录；没有记录时返回 None（按「现行」解释）。"""
    return db.execute(
        select(PartitionMode).where(
            PartitionMode.plan_run_id == plan_run_id,
            PartitionMode.platform == platform,
        )
    ).scalars().first()


def record_scan_round(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    round_no: int,
    kind: str,
    expected_set: Sequence[Dict[str, Any]],
    input_cutoff_at: datetime,
    status: str = SCAN_ROUND_STATUS_PENDING,
) -> ScanRound:
    """按 ``(plan_run_id, platform, round_no)`` 幂等登记轮次。"""
    _require_one_of(kind, _SCAN_ROUND_KINDS, "kind")
    _require_one_of(status, _SCAN_ROUND_STATUSES, "status")

    partition = get_partition_mode(db, plan_run_id=plan_run_id, platform=platform)
    if partition is not None and partition.shadow_settled_at is not None:
        raise ImmutableIssueFactError("shadow partition is settled")

    existing = db.execute(
        select(ScanRound).where(
            ScanRound.plan_run_id == plan_run_id,
            ScanRound.platform == platform,
            ScanRound.round_no == round_no,
        )
    ).scalars().first()
    if existing is not None:
        return existing

    row = ScanRound(
        plan_run_id=plan_run_id,
        platform=platform,
        round_no=round_no,
        kind=kind,
        expected_set=list(expected_set),
        input_cutoff_at=input_cutoff_at,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def set_scan_round_status(
    db: Session,
    *,
    round_id: int,
    status: str,
    closed_at: Optional[datetime] = None,
) -> ScanRound:
    """更新轮次状态；``CLOSED`` 时记录关闭时间。"""
    _require_one_of(status, _SCAN_ROUND_STATUSES, "status")

    row = db.get(ScanRound, round_id)
    if row is None:
        raise IssueFactError(f"scan_round {round_id} not found")

    row.status = status
    if status == SCAN_ROUND_STATUS_CLOSED:
        row.closed_at = closed_at or row.closed_at or _utcnow()
    db.flush()
    return row


def get_scan_round(db: Session, *, round_id: int) -> Optional[ScanRound]:
    """G2 轮次推进读取必须持行锁；调用方先锁 PlanRun，再读轮次。"""
    return db.execute(
        select(ScanRound).where(ScanRound.id == round_id).with_for_update()
        .execution_options(populate_existing=True),
    ).scalar_one_or_none()


def list_scan_rounds(
    db: Session, *, plan_run_id: int, platform: str,
) -> list[ScanRound]:
    """按轮次号加锁读取分区；首轮创建由调用方的 PlanRun 行锁串行化。"""
    return list(db.execute(
        select(ScanRound).where(
            ScanRound.plan_run_id == plan_run_id,
            ScanRound.platform == platform,
        ).order_by(ScanRound.round_no).with_for_update().execution_options(populate_existing=True),
    ).scalars())


def list_shadow_scan_run_ids(db: Session, *, platform: str) -> list[int]:
    """6619768806：发现 RUNNING 与已观察分区的终态 run，不追溯历史。

    EXISTS 只作候选发现；推进前仍按 PlanRun → scan_round 加锁复核。
    """
    observed = select(ScanRound.id).where(
        ScanRound.plan_run_id == PlanRun.id,
        ScanRound.platform == platform,
    ).exists()
    settled = select(PartitionMode.id).where(
        PartitionMode.plan_run_id == PlanRun.id,
        PartitionMode.platform == platform,
        PartitionMode.shadow_settled_at.isnot(None),
    ).exists()
    return list(db.execute(
        select(PlanRun.id).where(~settled, or_(
            PlanRun.status == "RUNNING",
            (PlanRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS", "FAILED"]) & observed),
        )).order_by(PlanRun.id),
    ).scalars())


def settle_shadow_partition(
    db: Session, *, plan_run_id: int, platform: str,
    settled_at: datetime, report_wait_seconds: int,
) -> PartitionMode:
    """调用方先锁 PlanRun 并确认无新迟到输入；只写一次 settled 时间。"""
    run = db.execute(select(PlanRun).where(
        PlanRun.id == plan_run_id,
    ).with_for_update()).scalar_one()
    partition = get_partition_mode(db, plan_run_id=plan_run_id, platform=platform)
    if partition is None:
        raise IssueFactError("shadow partition not found")
    if partition.shadow_settled_at is not None:
        return partition
    rounds = list_scan_rounds(db, plan_run_id=plan_run_id, platform=platform)
    if (
        run.status not in {"SUCCESS", "PARTIAL_SUCCESS", "FAILED"}
        or not any(r.kind == SCAN_ROUND_KIND_FINAL for r in rounds)
        or any(r.status != SCAN_ROUND_STATUS_CLOSED or r.closed_at is None for r in rounds)
        or settled_at <= max(r.closed_at for r in rounds) + timedelta(seconds=report_wait_seconds)
    ):
        raise IssueFactError("shadow partition is not ready to settle")
    partition.shadow_settled_at = settled_at
    db.flush()
    return partition


def record_scan_round_coverage(
    db: Session, *, round_id: int, coverage: Sequence[Dict[str, Any]],
) -> ScanRound:
    """只在开轮时更新既有期望项的覆盖；关闭后必须拒绝写入。"""
    row = get_scan_round(db, round_id=round_id)
    if row is None:
        raise IssueFactError(f"scan_round {round_id} not found")
    if row.status == SCAN_ROUND_STATUS_CLOSED:
        raise ImmutableIssueFactError(f"scan_round {round_id} coverage is closed")
    incoming = [(u["host_id"], u["platform"], u.get("expected", True)) for u in coverage]
    original = [(u["host_id"], u["platform"], u.get("expected", True)) for u in row.expected_set]
    if incoming != original:
        raise ImmutableIssueFactError("scan_round expected identities are immutable")
    for unit in coverage:
        _require_one_of(unit["coverage"], frozenset({"RECEIVED", "EMPTY", "MISSING", "LATE"}), "coverage")
    row.expected_set = [dict(unit) for unit in coverage]
    db.flush()
    return row


def list_partition_spill_inputs(
    db: Session, *, plan_run_id: int, platform: str, host_id: str,
) -> list[Dict[str, Any]]:
    """仅返回本分区、本 host 台账中仍在中心的已校验事件。"""
    rows = db.execute(
        select(DeviceLogEvent).join(SpillLedger, SpillLedger.dle_id == DeviceLogEvent.id).where(
            SpillLedger.plan_run_id == plan_run_id,
            func.lower(SpillLedger.platform) == platform.lower(),
            DeviceLogEvent.plan_run_id == plan_run_id,
            func.lower(DeviceLogEvent.platform) == platform.lower(),
            DeviceLogEvent.host_id == host_id,
            DeviceLogEvent.state.in_(["REMOTE", "ARCHIVED", "PRUNED"]),
            DeviceLogEvent.remote_path.isnot(None),
            DeviceLogEvent.remote_path != "",
        ).order_by(SpillLedger.id),
    ).scalars()
    return [{"dle_id": str(row.id), "remote_path": row.remote_path, "occurrence_key": row.occurrence_key} for row in rows]


def record_representative_selection(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    signature_id: int,
    selection_status: str,
    dle_id: Any = None,
    evidence_status: Optional[str] = None,
    selected_at: Optional[datetime] = None,
) -> RepresentativeSelection:
    """登记分区 × 问题的代表选择；进入「已选」后 ``dle_id`` 不可改（D7 第 6 条）。"""
    _require_one_of(selection_status, _SELECTION_STATUSES, "selection_status")
    if evidence_status is not None:
        _require_one_of(evidence_status, _EVIDENCE_STATUSES, "evidence_status")
    if selection_status == SELECTION_SELECTED and dle_id is None:
        raise ValueError("representative_selection.dle_id is required when selected")

    existing = db.execute(
        select(RepresentativeSelection).where(
            RepresentativeSelection.plan_run_id == plan_run_id,
            RepresentativeSelection.platform == platform,
            RepresentativeSelection.signature_id == signature_id,
        )
    ).scalars().first()

    if existing is None:
        row = RepresentativeSelection(
            plan_run_id=plan_run_id,
            platform=platform,
            signature_id=signature_id,
            dle_id=dle_id,
            selection_status=selection_status,
            evidence_status=evidence_status,
            selected_at=(
                selected_at
                or (_utcnow() if selection_status == SELECTION_SELECTED else None)
            ),
        )
        db.add(row)
        db.flush()
        return row

    if existing.selection_status == SELECTION_SELECTED:
        if selection_status != SELECTION_SELECTED:
            raise ImmutableIssueFactError(
                "representative_selection is immutable after selected: "
                f"plan_run_id={plan_run_id} platform={platform} "
                f"signature_id={signature_id}",
            )
        if dle_id is not None and existing.dle_id != dle_id:
            raise ImmutableIssueFactError(
                "representative_selection dle_id is immutable after selected: "
                f"existing={existing.dle_id} incoming={dle_id}",
            )
        if evidence_status is not None and existing.evidence_status != evidence_status:
            existing.evidence_status = evidence_status
        return existing

    existing.selection_status = selection_status
    if dle_id is not None:
        existing.dle_id = dle_id
    if evidence_status is not None:
        existing.evidence_status = evidence_status
    if selection_status == SELECTION_SELECTED:
        existing.selected_at = selected_at or existing.selected_at or _utcnow()
    db.flush()
    return existing


def record_delivery_version(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    version_no: int,
    software_versions: Optional[Sequence[Any]] = None,
    round_ids: Optional[Sequence[Any]] = None,
    basis_rounds: Optional[Sequence[Any]] = None,
    effective_coverage: Optional[Sequence[Any]] = None,
    issue_ids: Optional[Sequence[Any]] = None,
    event_ids: Optional[Sequence[Any]] = None,
    stats_snapshot_version: Optional[str] = None,
    pending_items: Optional[Sequence[Any]] = None,
    gaps: Optional[Sequence[Any]] = None,
    primary_folders: Optional[Sequence[Any]] = None,
    registered_at: Optional[datetime] = None,
) -> DeliveryVersion:
    """按 ``(plan_run_id, platform, version_no)`` 幂等登记交付物版本。"""
    existing = db.execute(
        select(DeliveryVersion).where(
            DeliveryVersion.plan_run_id == plan_run_id,
            DeliveryVersion.platform == platform,
            DeliveryVersion.version_no == version_no,
        )
    ).scalars().first()
    if existing is not None:
        return existing

    row = DeliveryVersion(
        plan_run_id=plan_run_id,
        platform=platform,
        version_no=version_no,
        software_versions=list(software_versions or []),
        round_ids=list(round_ids or []),
        basis_rounds=list(basis_rounds or []),
        effective_coverage=list(effective_coverage or []),
        issue_ids=list(issue_ids or []),
        event_ids=list(event_ids or []),
        stats_snapshot_version=stats_snapshot_version,
        pending_items=list(pending_items or []),
        gaps=list(gaps or []),
        primary_folders=list(primary_folders or []),
        registered_at=registered_at or _utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def record_spill_ledger_entry(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    dle_id: Any,
    size_bytes: int,
    spilled_at: datetime,
) -> SpillLedger:
    """按 ``dle_id`` 幂等登记溢出事件与字节数。"""
    existing = db.execute(
        select(SpillLedger).where(SpillLedger.dle_id == dle_id)
    ).scalars().first()
    if existing is not None:
        return existing

    row = SpillLedger(
        plan_run_id=plan_run_id,
        platform=platform,
        dle_id=dle_id,
        size_bytes=size_bytes,
        spilled_at=spilled_at,
    )
    db.add(row)
    db.flush()
    return row


def _occurrence_scope(
    stmt,
    *,
    signature_id: int,
    plan_run_id: Optional[int],
    platform: Optional[str],
):
    stmt = stmt.where(IssueOccurrence.signature_id == signature_id)
    if plan_run_id is not None:
        observed_in_run = select(IssueOccurrenceEvent.id).join(
            DeviceLogEvent, DeviceLogEvent.id == IssueOccurrenceEvent.dle_id,
        ).where(
            IssueOccurrenceEvent.occurrence_id == IssueOccurrence.id,
            DeviceLogEvent.plan_run_id == plan_run_id,
        ).exists()
        # The G4 candidate snapshot survives DLE/PlanRun retention, just like facts.
        persisted_in_run = select(ScanRound.id).where(
            ScanRound.plan_run_id == plan_run_id,
            ScanRound.merge_status == "OK",
            ScanRound.merge_summary["candidates"].contains(func.jsonb_build_array(
                func.jsonb_build_object("occurrence_id", IssueOccurrence.id),
            )),
        ).exists()
        stmt = stmt.where(or_(
            IssueOccurrence.first_seen_plan_run_id == plan_run_id,
            observed_in_run,
            persisted_in_run,
        ))
    if platform is not None:
        stmt = stmt.join(
            IssueSignature, IssueSignature.id == IssueOccurrence.signature_id,
        ).where(IssueSignature.platform == platform)
    return stmt


def count_occurrences(
    db: Session,
    *,
    signature_id: int,
    plan_run_id: Optional[int] = None,
    platform: Optional[str] = None,
) -> int:
    """按 ``occurrence_key`` 去重计数（I1）。

    身份不可证明（key 为空）的行按行计数——fail-open 下每条未证实观察都要保留，
    不能因为它们没有 key 就从次数里消失。
    """
    identity = func.coalesce(
        IssueOccurrence.occurrence_key,
        func.concat("unproven:", cast(IssueOccurrence.id, String)),
    )
    stmt = _occurrence_scope(
        select(func.count(distinct(identity))),
        signature_id=signature_id,
        plan_run_id=plan_run_id,
        platform=platform,
    )
    return int(db.execute(stmt).scalar_one())


def count_machines(
    db: Session,
    *,
    signature_id: int,
    plan_run_id: Optional[int] = None,
    platform: Optional[str] = None,
) -> int:
    """按设备 ``serial`` 去重计数。"""
    stmt = _occurrence_scope(
        select(func.count(distinct(IssueOccurrence.serial))),
        signature_id=signature_id,
        plan_run_id=plan_run_id,
        platform=platform,
    )
    return int(db.execute(stmt).scalar_one())


# G4: queries and writes remain behind the G1 service boundary.
def list_issue_signatures(db: Session, *, platform: str) -> list[dict]:
    return [{"id": r.id, "signature_raw": r.signature_raw} for r in db.execute(
        select(IssueSignature).where(func.lower(IssueSignature.platform) == platform.lower())
        .order_by(IssueSignature.id)
    ).scalars()]


def find_occurrence_by_key(db: Session, *, occurrence_key: str) -> Optional[IssueOccurrence]:
    return db.execute(select(IssueOccurrence).where(
        IssueOccurrence.occurrence_key == occurrence_key
    )).scalar_one_or_none()


def find_occurrence_by_dle(db: Session, *, dle_id: Any) -> Optional[IssueOccurrence]:
    return db.execute(select(IssueOccurrence).join(IssueOccurrenceEvent).where(
        IssueOccurrenceEvent.dle_id == dle_id
    ).order_by(IssueOccurrence.id).limit(1)).scalar_one_or_none()


def get_partition_event(db: Session, *, dle_id: Any, plan_run_id: int, platform: str, host_id: str):
    return db.execute(select(DeviceLogEvent).where(
        DeviceLogEvent.id == dle_id, DeviceLogEvent.plan_run_id == plan_run_id,
        func.lower(DeviceLogEvent.platform) == platform.lower(), DeviceLogEvent.host_id == host_id,
    )).scalar_one_or_none()


def get_representative_selection(db: Session, *, plan_run_id: int, platform: str, signature_id: int):
    return db.execute(select(RepresentativeSelection).where(
        RepresentativeSelection.plan_run_id == plan_run_id,
        RepresentativeSelection.platform == platform,
        RepresentativeSelection.signature_id == signature_id,
    )).scalar_one_or_none()


def record_scan_round_merge(
    db: Session, *, round_id: int, status: str, summary: dict,
    attempts: Optional[int] = None, next_retry_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> ScanRound:
    _require_one_of(status, frozenset({'NO_INPUT', 'OK', 'FAILED', 'FAILED_FINAL'}), 'merge_status')
    row = get_scan_round(db, round_id=round_id)
    if row is None or row.status != SCAN_ROUND_STATUS_CLOSED:
        raise IssueFactError('merge requires a closed scan round')
    if row.merge_status in SCAN_ROUND_MERGE_TERMINAL:
        return row
    row.merge_status, row.merge_summary, row.merged_at = status, summary, now or _utcnow()
    if status in SCAN_ROUND_MERGE_TERMINAL:
        # 只增不减：同一轮每进入一次终态加 1，与墙钟无关。
        row.merge_generation = (row.merge_generation or 0) + 1
    if attempts is not None:
        row.merge_attempts = attempts
    row.merge_next_retry_at = next_retry_at if status == 'FAILED' else None
    db.flush()
    return row


def reset_failed_final_shadow_merge(db: Session, *, round_id: int) -> ScanRound:
    """Explicit operator-only reset; scheduler and workers never call this entry point.

    merge_generation 不清零、不递减；重新进入终态时由 record_scan_round_merge 加 1。
    """
    row = get_scan_round(db, round_id=round_id)
    if row is None or row.merge_status != 'FAILED_FINAL':
        raise IssueFactError('manual merge reset requires FAILED_FINAL')
    row.merge_status = row.merge_summary = row.merged_at = row.merge_next_retry_at = None
    row.merge_attempts = 0
    db.flush()
    return row


def lock_shadow_merge(db: Session, *, plan_run_id: int):
    # Cross-run occurrence identity is global. Serialize G4 writers before run/round locks.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('b8-shadow-merge:mtk'))"))
    return db.execute(select(PlanRun).where(PlanRun.id == plan_run_id).with_for_update()).scalar_one_or_none()


def _pending_shadow_merge(now: datetime):
    return or_(ScanRound.merge_status.is_(None), and_(
        ScanRound.merge_status == 'FAILED',
        or_(ScanRound.merge_next_retry_at.is_(None), ScanRound.merge_next_retry_at <= now),
    ))


def pending_shadow_merge_ids(db: Session, *, plan_run_id: int, now: Optional[datetime] = None) -> list[int]:
    return list(db.execute(select(ScanRound.id).where(
        ScanRound.plan_run_id == plan_run_id, ScanRound.platform == 'mtk',
        ScanRound.status == SCAN_ROUND_STATUS_CLOSED,
        _pending_shadow_merge(now or _utcnow()),
    ).order_by(ScanRound.round_no)).scalars())


def list_partition_merge_candidates(db: Session, *, plan_run_id: int, platform: str) -> list[dict]:
    summaries = db.execute(select(ScanRound.merge_summary).where(
        ScanRound.plan_run_id == plan_run_id, ScanRound.platform == platform,
        ScanRound.merge_status == 'OK',
    ).order_by(ScanRound.round_no)).scalars()
    return [candidate for summary in summaries for candidate in (summary or {}).get('candidates', [])]



def list_pending_shadow_merge_run_ids(db: Session, *, now: Optional[datetime] = None) -> list[int]:
    """G4 retries remain discoverable after #3875 removes settled scan candidates."""
    return list(db.execute(select(ScanRound.plan_run_id).where(
        ScanRound.platform == 'mtk', ScanRound.status == SCAN_ROUND_STATUS_CLOSED,
        _pending_shadow_merge(now or _utcnow()),
    ).distinct().order_by(ScanRound.plan_run_id)).scalars())

# G5: delivery versions read the same facts through this service boundary.
def get_latest_delivery_version(
    db: Session, *, plan_run_id: int, platform: str,
) -> Optional[DeliveryVersion]:
    return db.execute(
        select(DeliveryVersion).where(
            DeliveryVersion.plan_run_id == plan_run_id,
            func.lower(DeliveryVersion.platform) == platform.lower(),
        ).order_by(DeliveryVersion.version_no.desc()).limit(1)
    ).scalar_one_or_none()


def list_partition_delivery_issues(
    db: Session, *, plan_run_id: int, platform: str,
) -> list[Dict[str, Any]]:
    """本分区（实际 PlanRun × 平台）的收录问题及发生统计（D9 第 2 条）。

    代表选择由 G4 写入；次数与机器数按 G1 的 count_occurrences /
    count_machines 口径（按发生 / 按 serial 去重），软件版本取本分区的
    发生记录。统计导出与交付物版本共用本函数，避免两处口径漂移。
    """
    selections = list(db.execute(
        select(RepresentativeSelection).where(
            RepresentativeSelection.plan_run_id == plan_run_id,
            func.lower(RepresentativeSelection.platform) == platform.lower(),
        ).order_by(RepresentativeSelection.signature_id)
    ).scalars())
    issues: list[Dict[str, Any]] = []
    for selection in selections:
        signature = db.get(IssueSignature, selection.signature_id)
        occurrences = list(db.execute(_occurrence_scope(
            select(IssueOccurrence),
            signature_id=selection.signature_id,
            plan_run_id=plan_run_id,
            platform=platform,
        )).scalars().unique())
        issues.append({
            "signature_id": selection.signature_id,
            "signature_raw": signature.signature_raw if signature is not None else None,
            "normalized_key": signature.normalized_key if signature is not None else None,
            "event_type": signature.event_type if signature is not None else None,
            "selection_status": selection.selection_status,
            "evidence_status": selection.evidence_status,
            "representative_dle_id": str(selection.dle_id) if selection.dle_id else None,
            "occurrences": count_occurrences(
                db, signature_id=selection.signature_id,
                plan_run_id=plan_run_id, platform=platform,
            ),
            "machines": count_machines(
                db, signature_id=selection.signature_id,
                plan_run_id=plan_run_id, platform=platform,
            ),
            "build_versions": sorted({
                occurrence.build_version for occurrence in occurrences
                if occurrence.build_version
            }),
        })
    return issues


def list_partition_delivery_event_ids(
    db: Session, *, plan_run_id: int, platform: str, signature_ids: Sequence[int],
) -> list[str]:
    """收录问题在本分区关联的全部 DLE（含同一事件的重复观察映射）。"""
    ids = [int(value) for value in signature_ids]
    if not ids:
        return []
    rows = db.execute(
        select(distinct(IssueOccurrenceEvent.dle_id)).join(
            DeviceLogEvent, DeviceLogEvent.id == IssueOccurrenceEvent.dle_id,
        ).where(
            IssueOccurrenceEvent.signature_id.in_(ids),
            DeviceLogEvent.plan_run_id == plan_run_id,
            func.lower(DeviceLogEvent.platform) == platform.lower(),
        )
    ).scalars()
    return sorted({str(row) for row in rows})


def list_shadow_delivery_due_run_ids(db: Session, *, platform: str) -> list[int]:
    """补偿发现：依据轮次的 (id, merge 代次) 尚未进入最新交付版本的 CURRENT 分区。

    代次单调、与墙钟无关；新轮次或同一轮重新进入终态都会改变 (id, 代次)。
    非 CURRENT、FINAL 未关闭或 merge 未终态、PlanRun 已不存在的分区直接排除。
    """
    latest = select(
        DeliveryVersion.plan_run_id.label("run_id"),
        DeliveryVersion.basis_rounds.label("basis_rounds"),
    ).where(
        func.lower(DeliveryVersion.platform) == platform.lower(),
    ).distinct(DeliveryVersion.plan_run_id).order_by(
        DeliveryVersion.plan_run_id,
        DeliveryVersion.version_no.desc(),
    ).subquery()
    final_round = aliased(ScanRound)
    final_terminal = select(final_round.id).where(
        final_round.plan_run_id == ScanRound.plan_run_id,
        func.lower(final_round.platform) == platform.lower(),
        final_round.kind == SCAN_ROUND_KIND_FINAL,
        final_round.status == SCAN_ROUND_STATUS_CLOSED,
        final_round.merge_status.in_(["OK", "NO_INPUT", "FAILED_FINAL"]),
    ).exists()
    non_current = select(PartitionMode.id).where(
        PartitionMode.plan_run_id == ScanRound.plan_run_id,
        func.lower(PartitionMode.platform) == platform.lower(),
        PartitionMode.mode != PARTITION_MODE_CURRENT,
    ).exists()
    plan_run_exists = select(PlanRun.id).where(
        PlanRun.id == ScanRound.plan_run_id,
    ).exists()
    basis = func.jsonb_build_array(func.jsonb_build_object(
        "round_id", ScanRound.id,
        "merge_generation", ScanRound.merge_generation,
    ))
    return list(db.execute(
        select(ScanRound.plan_run_id).select_from(ScanRound).outerjoin(
            latest, latest.c.run_id == ScanRound.plan_run_id,
        ).where(
            func.lower(ScanRound.platform) == platform.lower(),
            ScanRound.kind.in_([SCAN_ROUND_KIND_FINAL, SCAN_ROUND_KIND_SUPPLEMENT]),
            ScanRound.status == SCAN_ROUND_STATUS_CLOSED,
            ScanRound.merge_status.in_(["OK", "NO_INPUT", "FAILED_FINAL"]),
            final_terminal,
            ~non_current,
            plan_run_exists,
            or_(
                latest.c.run_id.is_(None),
                ~latest.c.basis_rounds.contains(basis),
            ),
        ).distinct().order_by(ScanRound.plan_run_id)
    ).scalars())


# ---------------------------------------------------------------------------
# B8 G6：S8 代表证据发布台账（ADR-0062 D8/D9）
# ---------------------------------------------------------------------------

PUBLICATION_STATE_PENDING = "PENDING"
PUBLICATION_STATE_RETRYING = "RETRYING"
PUBLICATION_STATE_VERIFIED = "VERIFIED"
PUBLICATION_STATE_FAILED = "FAILED"
_PUBLICATION_STATES = frozenset({
    PUBLICATION_STATE_PENDING,
    PUBLICATION_STATE_RETRYING,
    PUBLICATION_STATE_VERIFIED,
    PUBLICATION_STATE_FAILED,
})
_PUBLICATION_ACTIVE_STATES = frozenset({
    PUBLICATION_STATE_PENDING,
    PUBLICATION_STATE_RETRYING,
})
PUBLICATION_SOURCE_LOCAL = "LOCAL"
PUBLICATION_SOURCE_SPILL = "SPILL"
_PUBLICATION_SOURCES = frozenset({
    PUBLICATION_SOURCE_LOCAL,
    PUBLICATION_SOURCE_SPILL,
})


def record_evidence_publication(
    db: Session,
    *,
    plan_run_id: int,
    platform: str,
    dle_id: Any,
    root_plan_run_id: int,
    source_kind: str,
    source_path: Optional[str],
    collection_folder: Optional[str],
    naming_rule_version: Optional[str],
    destination_rel_path: Optional[str],
    executor_host_id: Optional[str] = None,
    state: str = PUBLICATION_STATE_PENDING,
    failure_reason: Optional[str] = None,
) -> EvidencePublication:
    """按 (plan_run_id, platform, dle_id) 幂等登记发布记录。

    身份一旦存在即返回原记录：同一 DLE 被多个问题选为代表时复用一条记录，
    不得因重放改写来源或目的路径。状态只允许从 PENDING/FAILED 起步；后续
    变化走 update_evidence_publication_state。
    """
    _require_one_of(source_kind, _PUBLICATION_SOURCES, "source_kind")
    if state not in {PUBLICATION_STATE_PENDING, PUBLICATION_STATE_FAILED}:
        raise ValueError(f"publication initial state invalid: {state!r}")
    if state == PUBLICATION_STATE_FAILED and not failure_reason:
        raise ValueError("publication FAILED requires failure_reason")
    if state != PUBLICATION_STATE_FAILED and not destination_rel_path:
        raise ValueError("active publication requires destination_rel_path")

    existing = db.execute(
        select(EvidencePublication).where(
            EvidencePublication.plan_run_id == plan_run_id,
            EvidencePublication.platform == platform,
            EvidencePublication.dle_id == dle_id,
        )
    ).scalars().first()
    if existing is not None:
        return existing

    row = EvidencePublication(
        plan_run_id=plan_run_id,
        platform=platform,
        dle_id=dle_id,
        root_plan_run_id=root_plan_run_id,
        source_kind=source_kind,
        source_path=source_path,
        collection_folder=collection_folder,
        naming_rule_version=naming_rule_version,
        destination_rel_path=destination_rel_path,
        state=state,
        failure_reason=failure_reason,
        executor_host_id=executor_host_id,
    )
    if state == PUBLICATION_STATE_FAILED:
        row.last_attempt_at = _utcnow()
    db.add(row)
    db.flush()
    return row


def get_evidence_publication(
    db: Session, *, publication_id: int, for_update: bool = False,
) -> Optional[EvidencePublication]:
    stmt = select(EvidencePublication).where(EvidencePublication.id == publication_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return db.execute(stmt).scalar_one_or_none()


def get_evidence_publication_for_dle(
    db: Session, *, plan_run_id: int, platform: str, dle_id: Any,
) -> Optional[EvidencePublication]:
    return db.execute(select(EvidencePublication).where(
        EvidencePublication.plan_run_id == plan_run_id,
        EvidencePublication.platform == platform,
        EvidencePublication.dle_id == dle_id,
    )).scalar_one_or_none()


def list_partition_evidence_publications(
    db: Session, *, plan_run_id: int, platform: str,
) -> list[EvidencePublication]:
    return list(db.execute(
        select(EvidencePublication).where(
            EvidencePublication.plan_run_id == plan_run_id,
            func.lower(EvidencePublication.platform) == platform.lower(),
        ).order_by(EvidencePublication.id)
    ).scalars())


def list_agent_evidence_publications(
    db: Session, *, host_id: str, limit: int = 50,
) -> list[EvidencePublication]:
    """Agent 拉取本机待办：LOCAL 源、仍在传输/重试中的记录。"""
    return list(db.execute(
        select(EvidencePublication).where(
            EvidencePublication.executor_host_id == host_id,
            EvidencePublication.source_kind == PUBLICATION_SOURCE_LOCAL,
            EvidencePublication.state.in_(sorted(_PUBLICATION_ACTIVE_STATES)),
        ).order_by(EvidencePublication.id).limit(limit)
    ).scalars())


def list_due_spill_publication_ids(
    db: Session, *, now: datetime, limit: int = 20,
) -> list[int]:
    """中心复制待办：SPILL 源、活动状态、到达重试时间。"""
    return list(db.execute(
        select(EvidencePublication.id).where(
            EvidencePublication.source_kind == PUBLICATION_SOURCE_SPILL,
            EvidencePublication.state.in_(sorted(_PUBLICATION_ACTIVE_STATES)),
            or_(
                EvidencePublication.next_attempt_at.is_(None),
                EvidencePublication.next_attempt_at <= now,
            ),
        ).order_by(EvidencePublication.id).limit(limit)
    ).scalars())


def list_evidence_publication_delivery_due_run_ids(
    db: Session, *, platform: str,
) -> list[int]:
    """回执已终态但最新交付版本尚未反映的分区（登记失败/重启补偿）。"""
    latest = select(func.max(DeliveryVersion.registered_at)).where(
        DeliveryVersion.plan_run_id == EvidencePublication.plan_run_id,
        func.lower(DeliveryVersion.platform) == func.lower(EvidencePublication.platform),
    ).correlate(EvidencePublication).scalar_subquery()
    return list(db.execute(
        select(EvidencePublication.plan_run_id).where(
            func.lower(EvidencePublication.platform) == platform.lower(),
            EvidencePublication.state.in_([
                PUBLICATION_STATE_VERIFIED, PUBLICATION_STATE_FAILED,
            ]),
            or_(latest.is_(None), EvidencePublication.updated_at > latest),
        ).distinct().order_by(EvidencePublication.plan_run_id)
    ).scalars())


def list_evidence_publication_archive_due_run_ids(
    db: Session, *, platform: str,
) -> list[int]:
    """已校验但代表 DLE 尚未归档的分区（登记成功后补归档）。"""
    return list(db.execute(
        select(EvidencePublication.plan_run_id).join(
            DeviceLogEvent, DeviceLogEvent.id == EvidencePublication.dle_id,
        ).where(
            func.lower(EvidencePublication.platform) == platform.lower(),
            EvidencePublication.state == PUBLICATION_STATE_VERIFIED,
            DeviceLogEvent.state.in_(["LOCAL", "REMOTE"]),
        ).distinct().order_by(EvidencePublication.plan_run_id)
    ).scalars())


def list_partition_selections(
    db: Session, *, plan_run_id: int, platform: str,
) -> list[RepresentativeSelection]:
    return list(db.execute(
        select(RepresentativeSelection).where(
            RepresentativeSelection.plan_run_id == plan_run_id,
            func.lower(RepresentativeSelection.platform) == platform.lower(),
        ).order_by(RepresentativeSelection.signature_id)
    ).scalars())


def list_selections_for_dle(
    db: Session, *, plan_run_id: int, platform: str, dle_id: Any,
) -> list[RepresentativeSelection]:
    """复用同一代表的全部问题（多个问题共享一条发布记录）。"""
    return list(db.execute(
        select(RepresentativeSelection).where(
            RepresentativeSelection.plan_run_id == plan_run_id,
            func.lower(RepresentativeSelection.platform) == platform.lower(),
            RepresentativeSelection.dle_id == dle_id,
            RepresentativeSelection.selection_status == SELECTION_SELECTED,
        ).order_by(RepresentativeSelection.signature_id)
    ).scalars())


def get_device_log_event(db: Session, *, dle_id: Any) -> Optional[DeviceLogEvent]:
    return db.get(DeviceLogEvent, dle_id)


def update_evidence_publication_state(
    db: Session,
    *,
    row: EvidencePublication,
    state: str,
    attempts: Optional[int] = None,
    checksum: Optional[str] = None,
    size_bytes: Optional[int] = None,
    failure_reason: Optional[str] = None,
    next_attempt_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> EvidencePublication:
    """推进发布状态；VERIFIED / FAILED 是终态，重复同态幂等。

    尝试次数只增不减（重启不清零）；FAILED 必须带原因；VERIFIED 必须带
    已复核的哈希与字节数。调用方负责行锁与事务。
    """
    _require_one_of(state, _PUBLICATION_STATES, "state")
    stamp = now or _utcnow()
    if row.state in {PUBLICATION_STATE_VERIFIED, PUBLICATION_STATE_FAILED}:
        if state != row.state:
            raise ImmutableIssueFactError(
                f"evidence_publication is terminal: {row.state} -> {state}",
            )
        if state == PUBLICATION_STATE_VERIFIED and checksum and row.checksum != checksum:
            raise ImmutableIssueFactError("verified checksum is immutable")
        return row

    if state == PUBLICATION_STATE_FAILED and not failure_reason:
        raise ValueError("publication FAILED requires failure_reason")
    if state == PUBLICATION_STATE_VERIFIED and (not checksum or size_bytes is None):
        raise ValueError("publication VERIFIED requires checksum and size_bytes")

    row.state = state
    if attempts is not None:
        row.attempts = max(int(row.attempts or 0), max(0, int(attempts)))
    row.last_attempt_at = stamp
    row.updated_at = stamp
    row.failure_reason = failure_reason if state == PUBLICATION_STATE_FAILED else None
    row.next_attempt_at = next_attempt_at if state == PUBLICATION_STATE_RETRYING else None
    if state == PUBLICATION_STATE_VERIFIED:
        row.checksum = checksum
        row.size_bytes = int(size_bytes)
        row.verified_at = stamp
    db.flush()
    return row
