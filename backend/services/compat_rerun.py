"""B8 G8：受控兼容补跑服务（ADR-0062 D15 第 5–7 条，§11 场景 10）。

设计不变量：

1. 仅限 Owner/admin 显式发起，目标必须为单个 ``DEDUP_FIRST`` 分区；
2. 只消费同一发生仍受 I4 保护的 Agent 本地原件或中心已校验副本；已 ``PRUNED``
   的事件不得回头读本地原件，且永远不把 ``DeviceLogEvent.state`` 改回 ``LOCAL``；
3. 独立登记 ``compat_rerun``（来源、覆盖、缺失清单、``is_partial``、独立产物），
   不覆盖原 ``DeliveryVersion`` 权威、不改写原 ``DLE.state``、不改变累计发生数；
4. 按 ``(plan_run_id, platform, request_key)`` 幂等；失败后同 ``request_key`` 重试
   只从原登记推进并递增 ``attempts``，不重复累加计数。

源选择顺序（同为已校验/仍可用的材料）：中心证据区已校验代表 → DLE
``remote_path`` 指向的中心事件目录 → 中心溢出副本 → I4 保护期内的 Agent 本地
原件。所有源都按实际 PlanRun、平台与所选交付版本的 issue/event 集合限定。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from sqlalchemy.orm import Session

from backend.agent.contracts.evidence_layout import (
    EvidenceLayoutError,
    assert_no_symlinks,
    copytree_no_symlinks,
)
from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_evidence_event_path,
    resolve_extract_event_src,
)
from backend.core.storage_root import resolve_shared_storage_root
from backend.models.device_log_event import DeviceLogEvent
from backend.models.issue_fact import CompatRerun
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts
from backend.services.dedup_scan import write_dedup_xls

logger = logging.getLogger(__name__)

_ADMIN_ROLES = frozenset({"admin", "owner"})
_COMPAT_ROOT_DIR = "compat_reruns"


@dataclass
class CompatRerunError(Exception):
    """受控兼容补跑校验或执行错误；由路由层转换为 HTTP 错误响应。"""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class CompatRerunOutcome:
    rerun: CompatRerun
    replayed: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _safe_under_root(candidate: Path, root: Path) -> Optional[Path]:
    try:
        resolved_root = root.resolve(strict=False)
        if candidate.is_symlink():
            return None
        resolved_cand = candidate.resolve(strict=False)
    except OSError:
        return None
    if not resolved_cand.is_relative_to(resolved_root) or not resolved_cand.exists():
        return None
    try:
        assert_no_symlinks(resolved_cand)
    except EvidenceLayoutError:
        return None
    return resolved_cand


def _tree_sha256(root_path: Path) -> str:
    digest = hashlib.sha256()
    if root_path.is_file():
        digest.update(root_path.read_bytes())
        return digest.hexdigest()
    for path in sorted(root_path.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(root_path).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _resolve_dle_compat_source(
    *,
    dle: DeviceLogEvent,
    root_plan_run_id: int,
    nfs_root: str,
    publication: Any,
    spill_record: Any,
    local_source_roots: Mapping[str, str],
) -> tuple[Optional[Path], Optional[str], str]:
    """按 I4 与中心校验优先级定位单个 DLE 的兼容补跑源材料。"""
    # 1. 中心证据区已校验代表（G6 发布台账）。
    if (
        publication is not None
        and publication.state == facts.PUBLICATION_STATE_VERIFIED
        and publication.destination_rel_path
    ):
        try:
            event_path = resolve_evidence_event_path(
                publication.destination_rel_path,
                root_plan_run_id=root_plan_run_id,
                serial=dle.serial,
                must_exist=True,
            )
            return event_path, "center_evidence_verified", "i4_center_verified"
        except ArtifactPathError:
            pass

    # 2. DLE.remote_path 指向的中心事件目录（上送校验成功后才写入）。
    if dle.remote_path:
        located = resolve_extract_event_src(
            dle.remote_path, nfs_root=nfs_root,
            plan_run_id=int(dle.plan_run_id or 0),
        )
        if located is not None:
            return located[0], "center_remote_verified", "i4_center_verified"

    # 3. 中心溢出副本（HddSpill 台账 + 发布记录源路径）。
    if (
        spill_record is not None
        and publication is not None
        and publication.source_kind == facts.PUBLICATION_SOURCE_SPILL
        and publication.source_path
    ):
        spill_root = Path(nfs_root) / "spill"
        candidate = _safe_under_root(Path(publication.source_path), spill_root)
        if candidate is not None:
            return candidate, "center_spill_verified", "i4_spill_protected"

    # 4. Agent 本地原件：只在未被 PRUNED 且仍在可读根下时使用。
    if str(dle.state or "").upper() != "PRUNED" and dle.local_path:
        host_root_raw = local_source_roots.get(str(dle.host_id))
        if host_root_raw:
            host_root = Path(host_root_raw)
            local_path = Path(dle.local_path)
            if not local_path.is_absolute():
                local_path = host_root / local_path
            candidate = _safe_under_root(local_path, host_root)
            if candidate is not None:
                return candidate, "agent_local_original", "i4_local_protected"

    return None, None, "missing_after_prune_or_unavailable"


def _scoped_issue_stats(pairs: list[tuple[Any, DeviceLogEvent]]) -> dict[int, dict]:
    """按交付版本范围计算每个问题的发生/机器数，避免跨版本串读。"""
    stats: dict[int, dict] = {}
    occurrences: dict[int, dict[int, Any]] = {}
    for occurrence, _dle in pairs:
        sig_id = int(occurrence.signature_id)
        occurrences.setdefault(sig_id, {})[int(occurrence.id)] = occurrence
    for sig_id, by_id in occurrences.items():
        values = list(by_id.values())
        stats[sig_id] = {
            "occurrences": len(values),
            "machines": len({occ.serial for occ in values if occ.serial}),
            "build_versions": sorted({
                occ.build_version for occ in values if occ.build_version
            }),
        }
    return stats


def trigger_compat_rerun(
    db: Session,
    *,
    plan_run_id: int,
    platform: str = "mtk",
    requested_by: str,
    requester_role: str,
    request_key: Optional[str] = None,
    delivery_version_no: Optional[int] = None,
    local_source_roots: Optional[Mapping[str, str]] = None,
    copy_fn: Optional[Callable[[Path, Path], None]] = None,
) -> CompatRerunOutcome:
    """Owner 显式发起单个 ``DEDUP_FIRST`` 分区的受控兼容补跑。"""
    if (requester_role or "").strip().lower() not in _ADMIN_ROLES:
        raise CompatRerunError(403, "owner/admin role required to trigger compat rerun")

    plat = (platform or "").strip().lower()
    if not plat:
        raise CompatRerunError(400, "platform is required")

    plan_run = db.get(PlanRun, plan_run_id)
    if plan_run is None:
        raise CompatRerunError(404, f"plan run {plan_run_id} not found")

    partition = facts.get_partition_mode(db, plan_run_id=plan_run_id, platform=plat)
    if partition is None or partition.mode != facts.PARTITION_MODE_DEDUP_FIRST:
        raise CompatRerunError(409, "compat rerun requires a DEDUP_FIRST partition")

    nfs_root = resolve_shared_storage_root()
    if not nfs_root:
        raise CompatRerunError(503, "shared storage root is not configured")

    if delivery_version_no is not None:
        delivery = facts.get_delivery_version(
            db, plan_run_id=plan_run_id, platform=plat,
            version_no=delivery_version_no,
        )
        if delivery is None:
            raise CompatRerunError(
                404,
                f"delivery version v{delivery_version_no} not found "
                f"for plan_run={plan_run_id}",
            )
    else:
        delivery = facts.get_latest_delivery_version(
            db, plan_run_id=plan_run_id, platform=plat,
        )

    resolved_key = (request_key or "").strip() or (
        f"compat-{plan_run_id}-{plat}-v{delivery.version_no if delivery else 0}"
    )
    existing = facts.get_compat_rerun_by_request(
        db, plan_run_id=plan_run_id, platform=plat, request_key=resolved_key,
    )
    if existing is not None:
        if existing.status == facts.COMPAT_RERUN_STATUS_COMPLETED:
            return CompatRerunOutcome(rerun=existing, replayed=True)
        if existing.status in {
            facts.COMPAT_RERUN_STATUS_PENDING,
            facts.COMPAT_RERUN_STATUS_RUNNING,
        }:
            raise CompatRerunError(
                409, f"compat rerun {existing.id} is currently {existing.status.lower()}",
            )
        rerun = existing
    else:
        rerun = facts.record_compat_rerun(
            db,
            plan_run_id=plan_run_id,
            platform=plat,
            request_key=resolved_key,
            requested_by=requested_by,
            source_delivery_version_id=delivery.id if delivery is not None else None,
            source_delivery_version_no=delivery.version_no if delivery is not None else None,
        )

    attempt_no = int(rerun.attempts or 0) + 1
    facts.update_compat_rerun(
        db, row=rerun,
        status=facts.COMPAT_RERUN_STATUS_PENDING,
        attempts=attempt_no,
        error=None,
    )
    db.flush()

    try:
        root_plan_run_id = int(plan_run.root_plan_run_id or plan_run.id)
        issue_ids = [int(i) for i in (delivery.issue_ids or [])] if delivery else None
        event_ids = [str(e) for e in (delivery.event_ids or [])] if delivery else None
        pairs = facts.list_partition_occurrences_for_scope(
            db,
            plan_run_id=plan_run_id,
            platform=plat,
            signature_ids=issue_ids,
            event_ids=event_ids,
        )
        all_issues = facts.list_partition_delivery_issues(
            db, plan_run_id=plan_run_id, platform=plat,
        )
        if issue_ids is not None:
            included = set(issue_ids)
            all_issues = [
                issue for issue in all_issues
                if int(issue["signature_id"]) in included
            ]
        stats = _scoped_issue_stats(pairs)

        publications = {
            str(row.dle_id): row
            for row in facts.list_partition_evidence_publications(
                db, plan_run_id=plan_run_id, platform=plat,
            )
        }

        dle_by_id: dict[str, DeviceLogEvent] = {}
        sigs_by_dle: dict[str, set[int]] = {}
        for occurrence, dle in pairs:
            key = str(dle.id)
            dle_by_id[key] = dle
            sigs_by_dle.setdefault(key, set()).add(int(occurrence.signature_id))

        artifact_dir = (
            Path(nfs_root).resolve(strict=False)
            / _COMPAT_ROOT_DIR
            / str(plan_run_id)
            / plat
            / f"r{rerun.id}"
        )
        events_dir = artifact_dir / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        copier = copy_fn or (
            lambda src, dst: copytree_no_symlinks(src, dst, dest_root=events_dir)
        )

        source_records: list[dict[str, Any]] = []
        missing_items: list[dict[str, Any]] = []
        copied_by_dle: dict[str, str] = {}
        local_roots = local_source_roots or {}

        for dle_id_str in sorted(dle_by_id):
            dle = dle_by_id[dle_id_str]
            publication = publications.get(dle_id_str)
            spill = facts.get_spill_ledger(db, dle_id=dle.id)
            src_path, source_kind, i4_status = _resolve_dle_compat_source(
                dle=dle,
                root_plan_run_id=root_plan_run_id,
                nfs_root=nfs_root,
                publication=publication,
                spill_record=spill,
                local_source_roots=local_roots,
            )
            if src_path is None or source_kind is None:
                missing_items.append({
                    "dle_id": dle_id_str,
                    "serial": dle.serial,
                    "host_id": dle.host_id,
                    "dle_state": dle.state,
                    "signature_ids": sorted(sigs_by_dle.get(dle_id_str, ())),
                    "reason": i4_status,
                })
                continue

            dest_name = src_path.name if src_path.is_dir() else dle_id_str
            dest_event_dir = events_dir / dle.serial / dest_name
            if dest_event_dir.exists():
                shutil.rmtree(dest_event_dir)
            if src_path.is_dir():
                copier(src_path, dest_event_dir)
            else:
                dest_event_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest_event_dir / src_path.name)

            copied_by_dle[dle_id_str] = str(dest_event_dir)
            source_records.append({
                "dle_id": dle_id_str,
                "serial": dle.serial,
                "host_id": dle.host_id,
                "original_dle_state": dle.state,
                "source_kind": source_kind,
                "i4_status": i4_status,
                "source_path": str(src_path),
                "copied_path": str(dest_event_dir),
                "digest_sha256": _tree_sha256(dest_event_dir),
            })

        is_partial = bool(missing_items)
        xls_rows: list[dict[str, Any]] = []
        for issue in all_issues:
            sig_id = int(issue["signature_id"])
            representative_dle = str(issue.get("representative_dle_id") or "")
            copied_path = copied_by_dle.get(representative_dle, "")
            if not copied_path:
                for dle_id_str, signature_ids in sorted(sigs_by_dle.items()):
                    if sig_id in signature_ids and dle_id_str in copied_by_dle:
                        copied_path = copied_by_dle[dle_id_str]
                        break
            issue_stats = stats.get(sig_id, {})
            versions = issue_stats.get("build_versions") or list(issue.get("build_versions") or [])
            occurrences = sorted(
                (
                    occurrence for occurrence, _dle in pairs
                    if int(occurrence.signature_id) == sig_id
                ),
                key=lambda item: (item.observed_at, str(item.id)),
            )
            exp_times = [
                occurrence.observed_at.isoformat()
                for occurrence in occurrences if occurrence.observed_at
            ]
            serials = sorted({
                occurrence.serial for occurrence in occurrences if occurrence.serial
            })
            xls_rows.append({
                "Id": str(sig_id),
                "Path": copied_path,
                "Version": ", ".join(versions),
                "ExpTime": exp_times[-1] if exp_times else "",
                "ExpClass": str(issue.get("event_type") or ""),
                "ExpType": str(issue.get("normalized_key") or ""),
                "CurProcess": str(issue.get("signature_raw") or ""),
                "Package": "",
                "Detail": str(issue.get("signature_raw") or ""),
                "CausedBy": "",
                "Count": int(issue_stats.get("occurrences", 0)),
                "DeviceId": ",".join(serials),
                "Device Count": int(issue_stats.get("machines", 0)),
            })

        merge_xls_path = write_dedup_xls(
            artifact_dir
            / f"Result_MergeFiles_Compat_{plan_run_id}_{plat}_r{rerun.id}.xls",
            xls_rows,
        )
        coverage_scope = {
            "total_events": len(dle_by_id),
            "covered_events": len(source_records),
            "missing_events": len(missing_items),
            "issue_ids": sorted(int(issue["signature_id"]) for issue in all_issues),
            "covered_dle_ids": sorted(copied_by_dle),
            "missing_dle_ids": sorted(item["dle_id"] for item in missing_items),
            "is_partial": is_partial,
        }
        manifest_path = artifact_dir / "compat_manifest.json"
        _atomic_write_json(manifest_path, {
            "schema": "stp.compat_rerun_manifest/v1",
            "compat_rerun_id": int(rerun.id),
            "plan_run_id": int(plan_run_id),
            "platform": plat,
            "request_key": resolved_key,
            "requested_by": requested_by,
            "source_delivery_version_id": rerun.source_delivery_version_id,
            "source_delivery_version_no": rerun.source_delivery_version_no,
            "is_partial": is_partial,
            "coverage_scope": coverage_scope,
            "source_records": source_records,
            "missing_items": missing_items,
            "merge_xls_path": str(merge_xls_path),
        })

        facts.update_compat_rerun(
            db, row=rerun,
            status=facts.COMPAT_RERUN_STATUS_COMPLETED,
            attempts=attempt_no,
            coverage_scope=coverage_scope,
            source_records=source_records,
            missing_items=missing_items,
            is_partial=is_partial,
            artifact_dir=str(artifact_dir),
            merge_xls_path=str(merge_xls_path),
            manifest_path=str(manifest_path),
            error=None,
            completed_at=_utcnow(),
        )
        db.flush()
        return CompatRerunOutcome(rerun=rerun, replayed=False)
    except Exception as exc:
        logger.exception(
            "compat_rerun_failed rerun_id=%d plan_run=%d platform=%s",
            rerun.id, plan_run_id, plat,
        )
        facts.update_compat_rerun(
            db, row=rerun,
            status=facts.COMPAT_RERUN_STATUS_FAILED,
            attempts=attempt_no,
            error=str(exc)[:1024],
        )
        db.flush()
        if isinstance(exc, CompatRerunError):
            raise
        raise CompatRerunError(500, f"compat rerun failed: {exc}") from exc


def list_compat_reruns_for_partition(
    db: Session, *, plan_run_id: int, platform: Optional[str] = None,
) -> list[CompatRerun]:
    return facts.list_compat_reruns(db, plan_run_id=plan_run_id, platform=platform)


__all__ = [
    "CompatRerunError",
    "CompatRerunOutcome",
    "list_compat_reruns_for_partition",
    "trigger_compat_rerun",
]
