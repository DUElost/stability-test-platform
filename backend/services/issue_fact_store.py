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

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from sqlalchemy import String, cast, distinct, func, select
from sqlalchemy.orm import Session

from backend.models.issue_fact import (
    DeliveryVersion,
    IssueOccurrence,
    IssueOccurrenceEvent,
    IssueSignature,
    PartitionMode,
    RepresentativeSelection,
    ScanRound,
    SpillLedger,
)


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
        stmt = stmt.where(IssueOccurrence.first_seen_plan_run_id == plan_run_id)
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
