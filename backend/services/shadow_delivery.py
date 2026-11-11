"""B8 G5：交付物版本登记与交付状态判定（ADR-0062 D9，影子阶段）。

只覆盖 MTK 影子分区：轮次进入终态后登记交付物版本，并生成统计导出。
交付状态不持久化（D9 第 5 条由当前事实推导）；阶段一没有 S8 回执，
判定函数在发布状态不适用时返回 scope_only，不返回就绪。

事实读写只经 G1 的 issue_fact_store；本模块不写 plan_run_artifact、
不改 DLE 状态、不触碰 dedup/{run} 的现行产物。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.contracts.round_report_layout import delivery_export_path
from backend.core.database import SessionLocal
from backend.core.settings.scheduler import get_scheduler_settings
from backend.core.storage_root import resolve_shared_storage_root
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts

logger = logging.getLogger(__name__)

PLATFORM_MTK = "mtk"
_TERMINAL_MERGE_STATUSES = frozenset({"OK", "NO_INPUT", "FAILED_FINAL"})
_FAILED_MERGE_STATUSES = frozenset({"FAILED", "FAILED_FINAL"})
_BASIS_ROUND_KINDS = frozenset({
    facts.SCAN_ROUND_KIND_FINAL,
    facts.SCAN_ROUND_KIND_SUPPLEMENT,
})
_COVERED = frozenset({"RECEIVED", "EMPTY"})

DELIVERY_STATUS_NOT_READY = "NOT_READY"
DELIVERY_STATUS_READY = "READY"
DELIVERY_STATUS_READY_WITH_GAPS = "READY_WITH_GAPS"
DELIVERY_STATUS_SCOPE_ONLY = "SCOPE_ONLY"

PUBLISH_VERIFIED = "VERIFIED"
PUBLISH_RETRYING = "RETRYING"
PUBLISH_FAILED = "FAILED"
_PUBLISH_STATUSES = frozenset({PUBLISH_VERIFIED, PUBLISH_RETRYING, PUBLISH_FAILED})

REASON_FINAL_ROUND_NOT_CLOSED = "final_round_not_closed"
REASON_LATEST_MERGE_PENDING = "latest_merge_pending"
REASON_LATEST_MERGE_FAILED = "latest_merge_failed"
REASON_LATEST_MERGE_NO_INPUT = "latest_merge_no_input"
REASON_EFFECTIVE_COVERAGE_EMPTY = "effective_coverage_empty"
REASON_PUBLISH_NOT_APPLICABLE = "publish_status_not_applicable"
REASON_REPRESENTATIVE_RETRYING = "representative_retrying"
REASON_GAPS_PRESENT = "gaps_present"
REASON_ALL_CHECKS_PASSED = "all_checks_passed"


@dataclass(frozen=True)
class DeliveryDecision:
    """D9 第 5 条的判定结果；gaps 与交付物版本使用同一套缺口结构。"""

    status: str
    reason: str
    gaps: tuple[dict, ...] = ()


@dataclass(frozen=True)
class DeliverySnapshot:
    plan_run_id: int
    platform: str
    round_ids: tuple[int, ...]
    effective_coverage: tuple[dict, ...]
    issue_ids: tuple[int, ...]
    event_ids: tuple[str, ...]
    software_versions: tuple[str, ...]
    pending_items: tuple
    gaps: tuple[dict, ...]
    primary_folders: tuple
    issues: tuple[dict, ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coverage_gaps(units: Sequence[Mapping[str, Any]]) -> list[dict]:
    return [
        {"kind": "coverage_missing", "host_id": unit["host_id"], "platform": unit["platform"]}
        for unit in units
        if not unit.get("covered")
    ]


def _representative_gaps(
    representatives: Sequence[Mapping[str, Any]],
    publish_status: Optional[Mapping[Any, str]],
) -> list[dict]:
    gaps: list[dict] = []
    for representative in representatives:
        if representative.get("selection_status") != facts.SELECTION_SELECTED:
            continue
        signature_id = representative.get("signature_id")
        if representative.get("evidence_status") == facts.EVIDENCE_INCOMPLETE:
            gaps.append({"kind": "evidence_incomplete", "signature_id": signature_id})
        if publish_status is not None and publish_status.get(signature_id) == PUBLISH_FAILED:
            gaps.append({"kind": "publish_failed", "signature_id": signature_id})
    return gaps


def evaluate_delivery_status(
    *,
    rounds: Sequence[Mapping[str, Any]],
    effective_coverage: Sequence[Mapping[str, Any]],
    representatives: Sequence[Mapping[str, Any]],
    publish_status: Optional[Mapping[Any, str]],
) -> DeliveryDecision:
    """按 D9 第 5 条的顺序判定交付状态，取第一个成立的结果。

    rounds 项至少包含 round_no、kind、status、merge_status。publish_status
    为 None 表示 S8 回执尚不适用（影子阶段）：第 1、2 步之后返回
    scope_only，绝不返回就绪。
    """
    if publish_status is not None:
        for value in publish_status.values():
            if value not in _PUBLISH_STATUSES:
                raise ValueError(f"publish status invalid: {value!r}")

    final = next((row for row in rounds if row.get("kind") == facts.SCAN_ROUND_KIND_FINAL), None)
    if final is None or final.get("status") != facts.SCAN_ROUND_STATUS_CLOSED:
        return DeliveryDecision(DELIVERY_STATUS_NOT_READY, REASON_FINAL_ROUND_NOT_CLOSED)

    basis = [
        row for row in rounds
        if row.get("kind") in _BASIS_ROUND_KINDS
        and row.get("status") == facts.SCAN_ROUND_STATUS_CLOSED
    ]
    latest = max(basis, key=lambda row: row.get("round_no", 0), default=None)
    merge_status = latest.get("merge_status") if latest is not None else None
    if merge_status in _FAILED_MERGE_STATUSES:
        return DeliveryDecision(DELIVERY_STATUS_NOT_READY, REASON_LATEST_MERGE_FAILED)
    if merge_status == "NO_INPUT":
        return DeliveryDecision(DELIVERY_STATUS_NOT_READY, REASON_LATEST_MERGE_NO_INPUT)
    if merge_status not in _TERMINAL_MERGE_STATUSES:
        return DeliveryDecision(DELIVERY_STATUS_NOT_READY, REASON_LATEST_MERGE_PENDING)

    if not any(unit.get("covered") for unit in effective_coverage):
        return DeliveryDecision(DELIVERY_STATUS_NOT_READY, REASON_EFFECTIVE_COVERAGE_EMPTY)

    if publish_status is None:
        return DeliveryDecision(DELIVERY_STATUS_SCOPE_ONLY, REASON_PUBLISH_NOT_APPLICABLE)

    selected = [
        representative for representative in representatives
        if representative.get("selection_status") == facts.SELECTION_SELECTED
    ]
    if any(
        publish_status.get(representative.get("signature_id")) == PUBLISH_RETRYING
        for representative in selected
    ):
        return DeliveryDecision(DELIVERY_STATUS_NOT_READY, REASON_REPRESENTATIVE_RETRYING)

    gaps = (
        _coverage_gaps(effective_coverage)
        + _representative_gaps(selected, publish_status)
    )
    if gaps:
        return DeliveryDecision(
            DELIVERY_STATUS_READY_WITH_GAPS, REASON_GAPS_PRESENT, tuple(gaps),
        )
    return DeliveryDecision(DELIVERY_STATUS_READY, REASON_ALL_CHECKS_PASSED)


def build_delivery_snapshot(
    db: Session, *, plan_run_id: int, platform: str,
) -> Optional[DeliverySnapshot]:
    """按当前事实计算交付物版本内容；没有终态最后一轮时返回 None。

    调用方必须已持有 PlanRun 行锁（facts.list_scan_rounds 会锁轮次行）。
    """
    if platform.lower() != PLATFORM_MTK:
        return None
    partition = facts.get_partition_mode(db, plan_run_id=plan_run_id, platform=platform)
    if partition is not None and partition.mode != facts.PARTITION_MODE_CURRENT:
        # 影子阶段只登记现行分区；没有模式记录的存量分区按现行解释。
        return None
    rounds = facts.list_scan_rounds(db, plan_run_id=plan_run_id, platform=platform)
    final = next(
        (row for row in rounds if row.kind == facts.SCAN_ROUND_KIND_FINAL), None,
    )
    if final is None or final.status != facts.SCAN_ROUND_STATUS_CLOSED:
        return None
    if final.merge_status not in _TERMINAL_MERGE_STATUSES:
        return None
    basis = [
        row for row in rounds
        if row.kind in _BASIS_ROUND_KINDS
        and row.status == facts.SCAN_ROUND_STATUS_CLOSED
        and row.merge_status in _TERMINAL_MERGE_STATUSES
    ]

    coverage: list[dict] = []
    for unit in final.expected_set:
        if not unit.get("expected", True):
            continue
        covering = []
        for row in basis:
            for candidate in row.expected_set:
                if (
                    candidate.get("host_id") == unit["host_id"]
                    and candidate.get("platform") == unit["platform"]
                    and candidate.get("coverage") in _COVERED
                ):
                    covering.append(row.id)
                    break
        coverage.append({
            "host_id": unit["host_id"],
            "platform": unit["platform"],
            "covered": bool(covering),
            "round_ids": covering,
        })
    coverage.sort(key=lambda unit: (unit["host_id"], unit["platform"]))

    issues = facts.list_partition_delivery_issues(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    issue_ids = tuple(issue["signature_id"] for issue in issues)
    event_ids = tuple(facts.list_partition_delivery_event_ids(
        db, plan_run_id=plan_run_id, platform=platform, signature_ids=issue_ids,
    ))
    software_versions = tuple(sorted({
        version for issue in issues for version in issue["build_versions"]
    })) or ("unknown",)
    gaps = _coverage_gaps(coverage) + _representative_gaps(issues, None)
    return DeliverySnapshot(
        plan_run_id=plan_run_id,
        platform=platform,
        round_ids=tuple(row.id for row in basis),
        effective_coverage=tuple(coverage),
        issue_ids=issue_ids,
        event_ids=event_ids,
        software_versions=software_versions,
        pending_items=(),
        gaps=tuple(gaps),
        primary_folders=(),
        issues=tuple(issues),
    )


def _canonical_coverage(units: Sequence[Mapping[str, Any]]) -> list[dict]:
    canonical = [
        {
            "host_id": unit.get("host_id"),
            "platform": unit.get("platform"),
            "covered": bool(unit.get("covered")),
            "round_ids": sorted(int(value) for value in unit.get("round_ids", [])),
        }
        for unit in units
    ]
    return sorted(canonical, key=lambda unit: (unit["host_id"], unit["platform"]))


def _canonical_gaps(gaps: Sequence[Mapping[str, Any]]) -> list[dict]:
    values = [dict(gap) for gap in gaps]
    return sorted(values, key=lambda gap: json.dumps(gap, sort_keys=True, ensure_ascii=False))


def _snapshot_signature(snapshot: DeliverySnapshot) -> dict:
    return {
        "round_ids": list(snapshot.round_ids),
        "effective_coverage": _canonical_coverage(snapshot.effective_coverage),
        "issue_ids": sorted(snapshot.issue_ids),
        "event_ids": sorted(snapshot.event_ids),
        "software_versions": list(snapshot.software_versions),
        "pending_items": list(snapshot.pending_items),
        "gaps": _canonical_gaps(snapshot.gaps),
        "primary_folders": list(snapshot.primary_folders),
    }


def _version_signature(row) -> dict:
    return {
        "round_ids": list(row.round_ids or []),
        "effective_coverage": _canonical_coverage(row.effective_coverage or []),
        "issue_ids": sorted(int(value) for value in (row.issue_ids or [])),
        "event_ids": sorted(str(value) for value in (row.event_ids or [])),
        "software_versions": list(row.software_versions or []),
        "pending_items": list(row.pending_items or []),
        "gaps": _canonical_gaps(row.gaps or []),
        "primary_folders": list(row.primary_folders or []),
    }


def _render_export(snapshot: DeliverySnapshot, version_no: int, generated_at: datetime) -> dict:
    return {
        "schema": "stp.delivery_stats",
        "schema_version": 1,
        "plan_run_id": snapshot.plan_run_id,
        "platform": snapshot.platform,
        "version_no": version_no,
        "generated_at": generated_at.isoformat(),
        "software_versions": list(snapshot.software_versions),
        "round_ids": list(snapshot.round_ids),
        "effective_coverage": [dict(unit) for unit in snapshot.effective_coverage],
        "gaps": [dict(gap) for gap in snapshot.gaps],
        "pending_items": list(snapshot.pending_items),
        "primary_folders": list(snapshot.primary_folders),
        "issues": [dict(issue) for issue in snapshot.issues],
        "event_ids": list(snapshot.event_ids),
    }


def _encode_export(payload: dict) -> bytes:
    text = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8")


def _write_export(path: Path, data: bytes) -> str:
    """原子写入（同目录 tmp + rename）；返回写入字节的 sha256。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False,
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return hashlib.sha256(data).hexdigest()


def evaluate_version_status(
    version,
    *,
    rounds: Sequence[Mapping[str, Any]],
    representatives: Sequence[Mapping[str, Any]],
    publish_status: Optional[Mapping[Any, str]],
) -> DeliveryDecision:
    """对已登记版本调用 D9 判定；版本提供有效覆盖，轮次提供状态与 merge 结果。"""
    return evaluate_delivery_status(
        rounds=rounds,
        effective_coverage=version.effective_coverage or [],
        representatives=representatives,
        publish_status=publish_status,
    )


def register_delivery_version(
    db: Session,
    *,
    plan_run_id: int,
    platform: str = PLATFORM_MTK,
    root: Optional[str] = None,
    now: Optional[datetime] = None,
):
    """登记新版本；内容与最新版本相同时不写库、不写文件。

    写入顺序固定为「导出文件先落盘，再插入登记行」：库失败只会留下可被
    下次覆盖的孤儿文件，不会出现有登记、无文件或哈希不一致。调用方负责
    提交事务；本函数已持有 PlanRun 行锁，登记与其他写入串行。
    """
    if platform.lower() != PLATFORM_MTK:
        return None
    if not get_scheduler_settings().stp_scan_round_shadow_enabled:
        return None
    if db.execute(
        select(PlanRun).where(PlanRun.id == plan_run_id).with_for_update()
    ).scalar_one_or_none() is None:
        return None
    snapshot = build_delivery_snapshot(db, plan_run_id=plan_run_id, platform=platform)
    if snapshot is None:
        return None

    latest = facts.get_latest_delivery_version(
        db, plan_run_id=plan_run_id, platform=platform,
    )
    if latest is not None and _version_signature(latest) == _snapshot_signature(snapshot):
        return latest

    version_no = (latest.version_no + 1) if latest is not None else 1
    storage_root = root or resolve_shared_storage_root()
    if not storage_root:
        raise facts.IssueFactError("shared storage root is not configured")
    path = delivery_export_path(storage_root, plan_run_id, platform, version_no)
    generated_at = now or _utcnow()
    data = _encode_export(_render_export(snapshot, version_no, generated_at))
    digest = _write_export(path, data)

    row = facts.record_delivery_version(
        db,
        plan_run_id=plan_run_id,
        platform=platform,
        version_no=version_no,
        software_versions=list(snapshot.software_versions),
        round_ids=list(snapshot.round_ids),
        effective_coverage=[dict(unit) for unit in snapshot.effective_coverage],
        issue_ids=list(snapshot.issue_ids),
        event_ids=list(snapshot.event_ids),
        stats_snapshot_version=digest,
        pending_items=list(snapshot.pending_items),
        gaps=[dict(gap) for gap in snapshot.gaps],
        primary_folders=list(snapshot.primary_folders),
        registered_at=generated_at,
    )
    if row.version_no != version_no or row.stats_snapshot_version != digest:
        raise facts.IssueFactError(
            "delivery_version registration mismatch: "
            f"version_no={row.version_no} expected={version_no}",
        )
    return row


def try_register_delivery_version(
    plan_run_id: int,
    *,
    platform: str = PLATFORM_MTK,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """合并任务与 sweep 的入口：失败只留专用日志，不向调用方抛出。"""
    if platform.lower() != PLATFORM_MTK:
        return None
    if not get_scheduler_settings().stp_scan_round_shadow_enabled:
        return None
    try:
        with SessionLocal() as db:
            row = register_delivery_version(
                db, plan_run_id=plan_run_id, platform=platform, now=now,
            )
            db.commit()
            return row.version_no if row is not None else None
    except Exception:
        logger.exception(
            "shadow_delivery_register_failed plan_run_id=%d platform=%s",
            plan_run_id, platform,
        )
        return None
