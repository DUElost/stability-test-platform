"""B8 G8：受控兼容补跑服务（ADR-0062 D15 第 5–7 条，§11 场景 10）。

设计不变量：
1. 仅限 Owner/admin 显式发起，目标必须为单个 ``DEDUP_FIRST`` 分区；
2. 仅消费同一发生仍受 I4 保护的本地原件或中心已校验副本（已 ``PRUNED`` 时绝不
   回头读 Agent 本地原件，且永远不把 ``DeviceLogEvent.state`` 改回 ``LOCAL``）；
3. 独立登记 ``compat_rerun``（来源、覆盖范围、缺失清单、``is_partial``、独立产物），
   不覆盖原 ``DeliveryVersion`` 权威、不改写原 ``DLE.state``、不改变累计发生计数；
4. 按 ``(plan_run_id, platform, request_key)`` 幂等；失败后同 ``request_key`` 重试
   只从登记记录推进并递增 ``attempts``，不重复累加计数。
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
from backend.models.issue_fact import CompatRerun, DeliveryVersion
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
    """触发或重放受控兼容补跑的结果。"""

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
    except Exception:
        return None
    return resolved_cand


def _tree_sha256(root_path: Path) -> str:
    digest = hashlib.sha256()
    if root_path.is_file():
        digest.update(root_path.read_bytes())
        return digest.hexdigest()
    for p in sorted(root_path.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(root_path).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(p.read_bytes())
    return digest.hexdigest()


def _resolve_dle_compat_source(
    *,
    dle: DeviceLogEvent,
    root_plan_run_id: int,
    nfs_root: str,
    publication: Any,
    spill_record: Any,
    protected_run_ids: set[int],
    local_source_roots: Mapping[str, str],
) -> tuple[Optional[Path], Optional[str], str]:
    """按 I4 与中心校验优先级定位单个 DLE 的兼容补跑源材料。"""
    # 1. Center verified evidence publication
    if (
        publication is not None
        and publication.state == facts.PUBLICATION_STATE_VERIFIED
        and publication.destination_rel_path
    ):
        try:
            ev_path = resolve_evidence_event_path(
                publication.destination_rel_path,
                root_plan_run_id=root_plan_run_id,
                serial=dle.serial,
                must_exist=True,
            )
            return ev_path, "center_evidence_verified", "i4_center_verified"
        except ArtifactPathError:
            pass

    # 2. Center verified remote_path under devices/{plan_run_id}/
    if dle.remote_path:
        located = resolve_extract_event_src(
            dle.remote_path, nfs_root=nfs_root, plan_run_id=int(dle.plan_run_id or 0),
        )
        if located is not None:
            src_dir, _ = located
            return src_dir, "center_remote_verified", "i4_center_verified"

    # 3. Center verified spill copy (spill ledger + publication.source_path)
    if (
        spill_record is not None
        and publication is not None
        and publication.source_kind == facts.PUBLICATION_SOURCE_SPILL
        and publication.source_path
    ):
        spill_root = Path(nfs_root) / "spill"
        cand = _safe_under_root(Path(publication.source_path), spill_root)
        if cand is not None:
            return cand, "center_spill_verified", "i4_spill_protected"

    # 4. Agent local original ONLY when not PRUNED (respect I4 boundary)
    if str(dle.state or "").upper() != "PRUNED" and dle.local_path:
        host_root_raw = local_source_roots.get(dle.host_id)
        if host_root_raw:
            host_root = Path(host_root_raw)
            local_p = Path(dle.local_path)
            if not local_p.is_absolute():
                local_p = host_root / local_p
            cand = _safe_under_root(local_p, host_root)
            if cand is not None:
                i4_tag = (
                    "i4_local_protected"
                    if int(dle.plan_run_id or 0) in protected_run_ids
                    else "local_original_available"
                )
                return cand, "agent_local_original", i4_tag

    return None, None, "missing_after_prune_or_unavailable"


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
        raise CompatRerunError(503, "NFS root not configured")

    dv: Optional[DeliveryVersion]
    if delivery_version_no is not None:
        dv = facts.get_delivery_version(
            db, plan_run_id=plan_run_id, platform=plat, version_no=delivery_version_no,
        )
        if dv is None:
            raise CompatRerunError(
                404, f"delivery version v{delivery_version_no} not found for plan_run={plan_run_id}",
            )
    else:
        dv = facts.get_latest_delivery_version(db, plan_run_id=plan_run_id, platform=plat)

    resolved_key = (request_key or "").strip() or (
        f"compat-{plan_run_id}-{plat}-v{dv.version_no if dv else 0}"
    )

    existing = facts.get_compat_rerun_by_request(
        db, plan_run_id=plan_run_id, platform=plat, request_key=resolved_key,
    )
    if existing is not None:
        if existing.status == facts.COMPAT_RERUN_STATUS_COMPLETED:
            return CompatRerunOutcome(rerun=existing, replayed=True)
        if existing.status == facts.COMPAT_RERUN_STATUS_PENDING:
            raise CompatRerunError(409, f"compat rerun {existing.id} is currently pending")
        rerun = existing
    else:
        rerun = facts.record_compat_rerun(
            db,
            plan_run_id=plan_run_id,
            platform=plat,
            request_key=resolved_key,
            requested_by=requested_by,
            source_delivery_version_id=dv.id if dv is not None else None,
            source_delivery_version_no=dv.version_no if dv is not None else None,
        )

    attempt_no = int(rerun.attempts or 0) + 1
    facts.update_compat_rerun(
        db,
        row=rerun,
        status=facts.COMPAT_RERUN_STATUS_PENDING,
        attempts=attempt_no,
        error=None,
    )
    db.flush()

    try:
        root_plan_run_id = int(plan_run.root_plan_run_id or plan_run.id)
        issue_ids = [int(i) for i in (dv.issue_ids or [])] if dv is not None else None
        event_ids = [str(e) for e in (dv.event_ids or [])] if dv is not None else None
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
            issue_id_set = set(issue_ids)
            all_issues = [i for i in all_issues if int(i["signature_id"]) in issue_id_set]

        pubs_by_dle = {
            str(p.dle_id): p
            for p in facts.list_partition_evidence_publications(
                db, plan_run_id=plan_run_id, platform=plat,
            )
        }
        protected_runs = facts.list_material_protected_run_ids(db, run_ids=[plan_run_id])

        # Deduplicate DLEs in scope and record mapping to signatures/occurrences.
        dle_by_id: dict[str, DeviceLogEvent] = {}
        sigs_by_dle: dict[str, set[int]] = {}
        occs_by_sig: dict[int, list[Any]] = {}
        for occ, dle in pairs:
            dle_key = str(dle.id)
            dle_by_id[dle_key] = dle
            sigs_by_dle.setdefault(dle_key, set()).add(int(occ.signature_id))
            occs_by_sig.setdefault(int(occ.signature_id), []).append(occ)

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
            pub = pubs_by_dle.get(dle_id_str)
            spill = facts.get_spill_ledger(db, dle_id=dle.id)
            src_path, source_kind, i4_status = _resolve_dle_compat_source(
                dle=dle,
                root_plan_run_id=root_plan_run_id,
                nfs_root=nfs_root,
                publication=pub,
                spill_record=spill,
                protected_run_ids=protected_runs,
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
            rep_dle = str(issue.get("representative_dle_id") or "")
            copied_path = copied_by_dle.get(rep_dle, "")
            if not copied_path:
                # Fallback only to another covered DLE belonging to the same signature in this rerun.
                for dle_id_str, sigs in sorted(sigs_by_dle.items()):
                    if sig_id in sigs and dle_id_str in copied_by_dle:
                        copied_path = copied_by_dle[dle_id_str]
                        break
            sig_occs = occs_by_sig.get(sig_id, [])
            serials = sorted({o.serial for o in sig_occs if o.serial})
            exp_times = sorted(o.observed_at.isoformat() for o in sig_occs if o.observed_at)
            xls_rows.append({
                "Id": str(sig_id),
                "Path": copied_path,
                "Version": ", ".join(issue.get("build_versions") or []),
                "ExpTime": exp_times[-1] if exp_times else "",
                "ExpClass": str(issue.get("event_type") or ""),
                "ExpType ": str(issue.get("normalized_key") or ""),
                "CurProcess": str(issue.get("signature_raw") or ""),
                "Package": "",
                "Detail": str(issue.get("signature_raw") or ""),
                "CausedBy": "",
                "Count": int(issue.get("occurrences") or 0),
                "DeviceId": ",".join(serials),
                "Device Count": int(issue.get("machines") or len(serials)),
            })

        merge_xls_path = write_dedup_xls(
            artifact_dir / f"Result_MergeFiles_Compat_{plan_run_id}_{plat}_r{rerun.id}.xls",
            xls_rows,
        )
        coverage_scope = {
            "total_events": len(dle_by_id),
            "covered_events": len(source_records),
            "missing_events": len(missing_items),
            "issue_ids": sorted(int(i["signature_id"]) for i in all_issues),
            "covered_dle_ids": sorted(copied_by_dle.keys()),
            "missing_dle_ids": sorted(m["dle_id"] for m in missing_items),
            "is_partial": is_partial,
        }
        manifest_path = artifact_dir / "compat_manifest.json"
        _atomic_write_json(
            manifest_path,
            {
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
            },
        )

        facts.update_compat_rerun(
            db,
            row=rerun,
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
            db,
            row=rerun,
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
    """查询分区的受控兼容补跑登记列表。"""
    return facts.list_compat_reruns(db, plan_run_id=plan_run_id, platform=platform)
