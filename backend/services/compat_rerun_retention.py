"""``compat_reruns/`` 派生族的逐笔留存判定与人工确认回收（#4045 第 2 刀）。

**为什么要单独一套判定**：``compat_reruns/{run}/{platform}/r{id}/`` 的目录按 run 编号
分，但产物不是 run 生命周期的一部分——它绑定「实际 PlanRun × 平台 × 一笔补跑登记」。
按 run 的无条件清理只遍历 ``RUN_FAMILIES``（见 ``backend/storage_families.py`` 与
``tests/test_center_storage_families_single_source.py`` 的守卫），所以这一族既不跟着 run
一起消失，也不能按链留存。**删除只能在本模块里逐笔判定**，且必须由人逐次确认。

候选的完整条件（缺一即保留，理由串逐条独立、不合并成一句话）：

1. 登记已终态 ``COMPLETED`` 且 ``completed_at`` 已知——``PENDING``/``RUNNING`` 视为在途；
   ``FAILED`` 按 Owner 裁决作为事故证据**一律保留**（理由 ``terminal_failed_evidence``，
   不再误标成「完成时间未知」）；
2. ``completed_at`` 早于 cutoff——补跑侧自身也必须超期，单边超期一律保留；
3. 所属 ``PlanRun`` 行存在、处于终态且 ``started_at`` 早于同一 cutoff。行缺失记
   ``plan_run_missing_unprovable`` 并**单列计数**：它是「无法证明」而不是「无需回收」，
   静默保留会让运维读数看起来干净（裁决 (b) 的快照列能消除它，Owner 选了 (a)+(c)，
   所以这里把不可证明性做成可观测项）；
4. 无 I4 保护（``list_material_protected_run_ids``：代表发布仍 PENDING/RETRYING、
   FINAL/SUPPLEMENT 轮未关闭）；
5. 无跨 run / 链引用（没有别的 PlanRun 把它当 parent/root）；
6. 产物目录真实存在、是普通目录、逐层无 symlink、resolve 后仍在族根之下。

**到期锚点（裁决 1 = (a)+(c)）**：本族自己的 ``COMPAT_RERUNS_RETENTION_DAYS``，
不继承 ``PLAN_RUN_RETENTION_DAYS`` 的生产覆盖（36500）——沿用同一旋钮会让候选恒空，
报表读起来像「没有要回收的东西」。因此报告必须自带 ``anchor_days`` / ``anchor_note``：
锚点异常长（≥10 年）或为 0 时给出显式警告，而不是留下一份空清单。

**自动窗口不在本刀**：本模块不接任何调度任务；回收只能通过
``backend/scripts/compat_rerun_retention.py`` 的显式 rerun id 列表发生，删除前用**当前
库状态**重判定一次（Dry-run 的输出不是删除授权），并受 ``COMPAT_RERUN_PURGE_BATCH_LIMIT``
限制。审计写失败即整笔失败（``strict=True``，ADR-0038 D2 语义）。
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.core.settings.scheduler import get_scheduler_settings
from backend.core.storage_root import (
    resolve_control_plane_storage_root,
    resolve_shared_storage_root,
)
from backend.models.issue_fact import CompatRerun, EvidencePublication
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts
from backend.services.audit_writer import record_audit
from backend.storage_families import COMPAT_RERUNS_FAMILY, storage_root_for_family

logger = logging.getLogger(__name__)

#: 与 cron 保留清理同词的 run 终态集合（`_retention_candidate_ids`）——本族判定不得另造口径。
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {"SUCCESS", "FAILED", "PARTIAL_SUCCESS"}
)
#: 锚点长到这个程度就必须在报告里警告（10 年）：候选恒空的真实原因是旋钮，不是没有垃圾。
ANCHOR_WARNING_DAYS = 3650

REASON_IN_FLIGHT = "in_flight"
REASON_FAILED_EVIDENCE = "terminal_failed_evidence"
REASON_NOT_COMPLETED = "status_not_completed"
REASON_COMPLETION_UNKNOWN = "completion_time_unknown"
REASON_RERUN_WITHIN_RETENTION = "rerun_within_retention"
REASON_PLAN_RUN_MISSING = "plan_run_missing_unprovable"
REASON_PLAN_RUN_NOT_TERMINAL = "plan_run_not_terminal"
REASON_PLAN_RUN_WITHIN_RETENTION = "plan_run_within_retention"
REASON_I4_PROTECTED = "i4_protected"
REASON_CHAIN_REFERENCED = "chain_referenced"
REASON_ARTIFACT_UNSAFE = "artifact_absent_or_unsafe"


@dataclass(frozen=True)
class CompatRerunRetentionError(Exception):
    """回收发起不合法（空列表 / 超批次上限）；由调用方转成显式失败，不部分执行。"""

    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 只用于日志与 CLI 输出
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class Usage:
    bytes: int = 0
    files: int = 0
    dirs: int = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _walk_usage(root: Path) -> Usage:
    """只读累计。单条目 stat 失败跳过——NFS 上一个坏 handle 不得中断整次盘点。"""
    if root.is_symlink() or not root.is_dir():
        return Usage()
    total_bytes = total_files = total_dirs = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        total_dirs += len(dirnames)
        for name in filenames:
            try:
                total_bytes += os.stat(os.path.join(current, name)).st_size
            except OSError:
                continue
            total_files += 1
    return Usage(bytes=total_bytes, files=total_files, dirs=total_dirs + 1)


def _settings_days_and_limit(
    retention_days: Optional[int], batch_limit: Optional[int]
) -> tuple[int, int]:
    settings = get_scheduler_settings()
    days = int(settings.compat_reruns_retention_days if retention_days is None else retention_days)
    limit = int(
        settings.compat_rerun_purge_batch_limit if batch_limit is None else batch_limit
    )
    return days, limit


def _family_root(log_root: Optional[str]) -> Path:
    resolved_log = log_root if log_root is not None else resolve_shared_storage_root()
    if not resolved_log:
        raise CompatRerunRetentionError(
            "ROOT_UNSET", "log root is not configured (STP_AEE_NFS_ROOT)"
        )
    base = Path(resolved_log)
    return Path(storage_root_for_family(
        COMPAT_RERUNS_FAMILY, log_root=str(base),
        control_plane_root=resolve_control_plane_storage_root() or str(base),
    )) / COMPAT_RERUNS_FAMILY


def leaf_dir(family_root: Path, *, plan_run_id: int, platform: str, rerun_id: int) -> Optional[Path]:
    """登记对应的产物叶子目录；任一层是 symlink、缺层或解析后逃逸族根 ⇒ ``None``。

    ``platform`` 直接来自登记行，不做白名单猜测：越根判定靠 resolve 后的相对性，
    而不是靠字符检查——两条路径都要成立才算安全。
    """
    target = family_root / str(int(plan_run_id)) / str(platform) / f"r{int(rerun_id)}"
    try:
        root_resolved = family_root.resolve()
        resolved = target.resolve(strict=True)
    except OSError:
        return None
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None
    current = family_root
    for part in target.relative_to(family_root).parts:
        current = current / part
        if current.is_symlink():
            return None
    return target if target.is_dir() else None


def _chain_referenced_run_ids(db: Session, run_ids: Sequence[int]) -> set[int]:
    """被**本批之外**的 PlanRun 当作 parent/root 引用的 run（链留存，B8 G7 同判据）。"""
    ids = [int(value) for value in run_ids]
    if not ids:
        return set()
    rows = db.execute(
        select(PlanRun.parent_plan_run_id, PlanRun.root_plan_run_id).where(
            or_(
                PlanRun.parent_plan_run_id.in_(ids),
                PlanRun.root_plan_run_id.in_(ids),
            ),
            PlanRun.id.notin_(ids),
        )
    ).all()
    return {int(ref) for refs in rows for ref in refs if ref is not None and int(ref) in ids}


def _evidence_summary(db: Session, run_ids: Sequence[int]) -> dict[tuple[int, str], dict[str, int]]:
    """证据台账摘要（对拍用）：总数 / 已校验 / 未收口。

    未收口 > 0 的 run 一定同时被 ``i4_protected`` 拦住——摘要出现是为了让 Owner 逐次确认时
    能把「候选目录 ↔ 证据」对上，不是为了改变判定。
    """
    ids = sorted({int(value) for value in run_ids})
    if not ids:
        return {}
    out: dict[tuple[int, str], dict[str, int]] = {}
    for run_id, platform, state, count in db.execute(
        select(
            EvidencePublication.plan_run_id, EvidencePublication.platform,
            EvidencePublication.state, func.count(),
        ).where(EvidencePublication.plan_run_id.in_(ids))
        .group_by(EvidencePublication.plan_run_id, EvidencePublication.platform,
                  EvidencePublication.state)
    ).all():
        bucket = out.setdefault(
            (int(run_id), str(platform)), {"total": 0, "verified": 0, "unresolved": 0},
        )
        bucket["total"] += int(count)
        if state == facts.PUBLICATION_STATE_VERIFIED:
            bucket["verified"] += int(count)
        elif state in (facts.PUBLICATION_STATE_PENDING, facts.PUBLICATION_STATE_RETRYING):
            bucket["unresolved"] += int(count)
    return out


def _candidate_payload(row: CompatRerun, artifact: Path, usage: Usage, *, evidence: dict[str, int]) -> dict[str, Any]:
    """四路对拍：候选目录 ↔ 原交付 ↔ 证据 ↔ 共享源，全部落在同一条记录里。"""
    records = list(row.source_records or [])
    return {
        "rerun_id": int(row.id),
        "plan_run_id": int(row.plan_run_id),
        "platform": str(row.platform),
        "request_key": str(row.request_key),
        "requested_by": str(row.requested_by),
        "status": str(row.status),
        "completed_at": _aware(row.completed_at).isoformat(),
        "artifact_dir": str(artifact),
        "bytes": usage.bytes,
        "files": usage.files,
        "dirs": usage.dirs,
        "merge_xls_path": row.merge_xls_path,
        "merge_xls_present": bool(row.merge_xls_path and Path(row.merge_xls_path).is_file()),
        "manifest_path": row.manifest_path,
        "manifest_present": bool(row.manifest_path and Path(row.manifest_path).is_file()),
        "coverage_scope": row.coverage_scope or {},
        "missing_items": list(row.missing_items or []),
        "source_delivery_version": {
            "id": row.source_delivery_version_id,
            "no": row.source_delivery_version_no,
        },
        "shared_sources": [
            {
                "dle_id": record.get("dle_id"),
                "source_kind": record.get("source_kind"),
                "source_path": record.get("source_path"),
                "copied_path": record.get("copied_path"),
                "digest_sha256": record.get("digest_sha256"),
            }
            for record in records
        ],
        "evidence": evidence,
    }


def evaluate_compat_rerun_retention(
    db: Session,
    *,
    now: Optional[datetime] = None,
    retention_days: Optional[int] = None,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """只读盘点：返回 ``candidates`` / ``retained`` / ``purged`` 三栏与计数。"""
    resolved_now = _aware(now) or _utcnow()
    days, _limit = _settings_days_and_limit(retention_days, None)
    cutoff = resolved_now - timedelta(days=days)
    family_root = _family_root(log_root)

    rows = list(db.execute(select(CompatRerun).order_by(CompatRerun.id)).scalars())
    run_ids = sorted({int(row.plan_run_id) for row in rows})
    runs = {
        int(run.id): run
        for run in db.execute(select(PlanRun).where(PlanRun.id.in_(run_ids))).scalars()
    } if run_ids else {}
    protected = facts.list_material_protected_run_ids(db, run_ids=run_ids) if run_ids else set()
    chain_refs = _chain_referenced_run_ids(db, run_ids)
    evidence = _evidence_summary(db, run_ids)

    candidates: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    purged: list[dict[str, Any]] = []

    for row in rows:
        run_id = int(row.plan_run_id)
        base = {
            "rerun_id": int(row.id),
            "plan_run_id": run_id,
            "platform": str(row.platform),
            "status": str(row.status),
        }
        purged_at = _aware(row.artifact_purged_at)
        if purged_at is not None:
            # 已回收：不再判定，也不出现在「保留」里制造噪音（裁决 3 = 3a）。
            purged.append({**base, "purged_at": purged_at.isoformat()})
            continue

        reasons: list[str] = []
        completed_at = _aware(row.completed_at)
        status = str(row.status or "").upper()
        if status in (facts.COMPAT_RERUN_STATUS_PENDING, facts.COMPAT_RERUN_STATUS_RUNNING):
            reasons.append(REASON_IN_FLIGHT)
        elif status == facts.COMPAT_RERUN_STATUS_FAILED:
            reasons.append(REASON_FAILED_EVIDENCE)
        elif status != facts.COMPAT_RERUN_STATUS_COMPLETED:
            reasons.append(REASON_NOT_COMPLETED)
        elif completed_at is None:
            reasons.append(REASON_COMPLETION_UNKNOWN)
        elif completed_at >= cutoff:
            reasons.append(REASON_RERUN_WITHIN_RETENTION)

        run = runs.get(run_id)
        if run is None:
            reasons.append(REASON_PLAN_RUN_MISSING)
        else:
            if str(run.status) not in TERMINAL_RUN_STATUSES:
                reasons.append(REASON_PLAN_RUN_NOT_TERMINAL)
            elif _aware(run.started_at) is None or _aware(run.started_at) >= cutoff:
                reasons.append(REASON_PLAN_RUN_WITHIN_RETENTION)

        if run_id in protected:
            reasons.append(REASON_I4_PROTECTED)
        if run_id in chain_refs:
            reasons.append(REASON_CHAIN_REFERENCED)

        artifact = leaf_dir(
            family_root, plan_run_id=run_id, platform=row.platform, rerun_id=int(row.id),
        )
        if artifact is None:
            reasons.append(REASON_ARTIFACT_UNSAFE)

        if reasons:
            retained.append({
                **base,
                "completed_at": completed_at.isoformat() if completed_at else None,
                "reasons": sorted(set(reasons)),
            })
            continue

        candidates.append(_candidate_payload(
            row, artifact, _walk_usage(artifact),
            evidence=evidence.get((run_id, str(row.platform)),
                                  {"total": 0, "verified": 0, "unresolved": 0}),
        ))

    counts = {
        "registered": len(rows),
        "candidates": len(candidates),
        "retained": len(retained),
        "purged": len(purged),
        # 不可证明性必须可见：它说明「候选为空」不等于「无需回收」。
        "unprovable_missing_run": sum(
            1 for item in retained if REASON_PLAN_RUN_MISSING in item["reasons"]
        ),
        "candidate_bytes": sum(int(item["bytes"]) for item in candidates),
    }
    anchor_note = None
    if days == 0:
        anchor_note = "anchor=0：cutoff=now，该层全部到期（沿用 PlanRun/audit 家族语义）"
    elif days >= ANCHOR_WARNING_DAYS:
        anchor_note = (
            f"anchor={days}d（≥{ANCHOR_WARNING_DAYS}）：候选几乎必然为空，"
            "这是旋钮取值问题，不是「没有要回收的产物」"
        )
    return {
        "generated_at": resolved_now.isoformat(),
        "anchor_days": days,
        "anchor_source": "COMPAT_RERUNS_RETENTION_DAYS",
        "cutoff": cutoff.isoformat(),
        "family_root": str(family_root),
        "anchor_note": anchor_note,
        "counts": counts,
        "candidates": candidates,
        "retained": retained,
        "purged": purged,
    }


def purge_compat_rerun_artifacts(
    db: Session,
    *,
    rerun_ids: Sequence[int],
    operator: str,
    now: Optional[datetime] = None,
    retention_days: Optional[int] = None,
    batch_limit: Optional[int] = None,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """按显式 rerun id 逐笔回收；每笔删除前用**当前库状态**重判定。

    只删 ``r{id}/`` 叶子与清空后的 ``{platform}/``、``{run}/`` 空父目录（族根永不删）；
    不删登记行、不改状态，不触碰 ``evidence/``、``devices/``、``dedup/`` 与共享源。
    空列表与超上限**整笔拒绝**，不做部分执行——「顺手删前 N 个」不是人工确认的语义。
    """
    requested = sorted({int(value) for value in rerun_ids})
    if not requested:
        raise CompatRerunRetentionError("EMPTY_SELECTION", "purge requires explicit rerun ids")
    days, limit = _settings_days_and_limit(retention_days, batch_limit)
    if len(requested) > limit:
        raise CompatRerunRetentionError(
            "BATCH_LIMIT_EXCEEDED",
            f"{len(requested)} rerun ids exceed COMPAT_RERUN_PURGE_BATCH_LIMIT={limit}",
        )

    report = evaluate_compat_rerun_retention(
        db, now=now, retention_days=days, log_root=log_root,
    )
    eligible = {int(item["rerun_id"]): item for item in report["candidates"]}
    reasons = {int(item["rerun_id"]): item["reasons"] for item in report["retained"]}

    removed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for rerun_id in requested:
        item = eligible.get(rerun_id)
        if item is None:
            skipped.append({
                "rerun_id": rerun_id,
                "reasons": reasons.get(rerun_id, ["not_candidate_or_not_registered"]),
            })
            continue
        leaf = Path(item["artifact_dir"])
        family_root = Path(report["family_root"])
        try:
            shutil.rmtree(leaf)
        except OSError as exc:
            # 目录没删掉：一行都不写，避免「标记说已回收、文件还在」的静默泄漏。
            failed.append({"rerun_id": rerun_id, "error": str(exc), "committed": False})
            continue
        for parent in (leaf.parent, leaf.parent.parent):
            # 只清「本次腾空」的父目录：族根永不删，也不允许删到族根之外。
            if parent == family_root or not parent.is_relative_to(family_root):
                break
            try:
                parent.rmdir()
            except OSError:
                break
        try:
            with db.begin_nested():
                row = db.get(CompatRerun, rerun_id)
                if row is not None:
                    facts.mark_compat_rerun_artifact_purged(db, row=row, now=_utcnow())
                record_audit(
                    db,
                    action="compat_rerun_purge",
                    resource_type="compat_rerun",
                    resource_id=rerun_id,
                    username=operator,
                    details={
                        "plan_run_id": item["plan_run_id"],
                        "platform": item["platform"],
                        "request_key": item["request_key"],
                        "artifact_dir": item["artifact_dir"],
                        "bytes": item["bytes"],
                        "files": item["files"],
                        "anchor_days": report["anchor_days"],
                    },
                    strict=True,
                )
        except Exception as exc:  # 审计或标记失败：如实报告「目录已删、标记未落」
            db.rollback()
            failed.append({
                "rerun_id": rerun_id, "error": str(exc), "committed": False,
                "artifact_dir": item["artifact_dir"], "already_removed": True,
            })
            logger.warning(
                "compat_rerun_purge_marker_failed rerun=%s dir=%s", rerun_id, item["artifact_dir"],
            )
            continue
        db.commit()
        removed.append({
            "rerun_id": rerun_id,
            "plan_run_id": item["plan_run_id"],
            "platform": item["platform"],
            "artifact_dir": item["artifact_dir"],
            "bytes": item["bytes"],
            "files": item["files"],
        })

    return {
        "anchor_days": report["anchor_days"],
        "cutoff": report["cutoff"],
        "batch_limit": limit,
        "requested": requested,
        "removed": removed,
        "skipped": skipped,
        "failed": failed,
    }
