"""B8 G8：按交付版本 / 受控兼容补跑绑定的 Jira 提单输入、隔离工作区与可验证回写（ADR-0062 D10/D15）。

规则摘要：
1. ``source="delivery_version"`` 严格绑定 ``PlanRun × platform × DeliveryVersion``，
   从已登记版本及其统计导出/事实库取得问题、发生与代表证据范围，绝不扫描
   物理链根目录反推。
2. ``source="compat_rerun"`` 绑定已完成（``COMPLETED``）的 ``CompatRerun``，
   且同一兼容补跑仅允许一次有效 ``create`` 提单（防重复建单）。
3. 厂商工具必须在每次 run 独立的 ``workspace_dir`` 中运行（``cwd=workspace_dir``），
   不得把厂商中间/结果写回主文件夹或中心 ``devices/``。
4. 结果回写只在存在可验证的发生证据时写入 ``JiraOccurrenceLink`` 与
   ``IssueSignature.issue_keys``；绝不按日志行顺序猜配，无法证实对应关系时
   在 ``JiraRun.attribution_gaps`` 记录可检索缺口。
5. ``FAILED`` PlanRun 的自动路径不允许生成提单清单或自动启动 ``JiraRun``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import xlrd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.core.artifact_paths import ArtifactPathError, resolve_evidence_event_path
from backend.core.storage_root import resolve_shared_storage_root
from backend.models.enums import PlanRunStatus
from backend.models.issue_fact import CompatRerun, DeliveryVersion
from backend.models.jira_run import JiraOccurrenceLink, JiraRun
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts
from backend.services.dedup_scan import write_dedup_xls
from backend.services.jira_issue_parser import parse_issue_keys
from backend.services.shadow_delivery import delivery_export_path

logger = logging.getLogger(__name__)

SOURCE_DELIVERY_VERSION = "delivery_version"
SOURCE_COMPAT_RERUN = "compat_rerun"

ATTRIBUTION_VERIFIED = "VERIFIED"
ATTRIBUTION_PARTIAL = "PARTIAL"
ATTRIBUTION_UNATTRIBUTED = "UNATTRIBUTED"
ATTRIBUTION_NOT_APPLICABLE = "NOT_APPLICABLE"

_ISSUE_KEY_EXACT_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,}-\d+$")
_SAFE_STAGE_TERMINAL = frozenset({"SUCCESS", "FAILED", "CANCELED"})
_TERMINAL_CREATE_ACTIVE = frozenset({"RUNNING", "SUCCESS"})


@dataclass
class JiraDeliveryError(Exception):
    """提单绑定/校验失败；由路由层映射为对应 HTTP 状态码。"""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class ResolvedJiraDeliveryInput:
    """经过校验并收窄到登记范围的提单输入载荷。"""

    source: str
    plan_run_id: int
    root_plan_run_id: int
    platform: str
    delivery_version_id: Optional[int]
    delivery_version_no: Optional[int]
    compat_rerun_id: Optional[int]
    input_source_label: str
    manifest: dict[str, Any]
    xls_rows: tuple[dict[str, Any], ...]
    source_xls_path: Optional[Path] = None
    uploaded_bytes: Optional[bytes] = None
    uploaded_suffix: Optional[str] = None


@dataclass(frozen=True)
class PreparedJiraWorkspace:
    """每次 run 独立、可写的厂商工具隔离工作目录。"""

    workspace_dir: Path
    input_xls: Path
    manifest_path: Path


def allow_auto_jira_for_plan_run(
    db: Session,
    *,
    plan_run_id: int,
    partition_mode: Optional[str] = None,
) -> bool:
    """ADR-0062 D15 第 2–3 条：``FAILED`` PlanRun 的自动路径禁止生成提单清单或自动 ``JiraRun``。"""
    plan_run = db.get(PlanRun, plan_run_id)
    if plan_run is None:
        return False
    status_val = str(getattr(plan_run, "status", "") or "").upper()
    if status_val == PlanRunStatus.FAILED.value:
        logger.info(
            "auto_jira_blocked_for_failed_plan_run plan_run=%d partition_mode=%s",
            plan_run_id, partition_mode or "unknown",
        )
        return False
    return True


def _normalize_platform(platform: Optional[str]) -> str:
    val = (platform or "").strip().lower()
    if not val:
        raise JiraDeliveryError(400, "platform is required")
    return val


def _load_delivery_export_if_present(
    *,
    nfs_root: Optional[str],
    plan_run_id: int,
    platform: str,
    version_no: int,
) -> Optional[dict[str, Any]]:
    if not nfs_root:
        return None
    expected = delivery_export_path(
        str(Path(nfs_root).resolve(strict=False)), plan_run_id, platform, version_no,
    )
    if not expected.exists():
        return None
    if expected.is_symlink() or expected.resolve(strict=False) != expected:
        raise JiraDeliveryError(400, "delivery export path must not follow symlinks")
    try:
        payload = json.loads(expected.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JiraDeliveryError(409, f"delivery export is unreadable: {exc}") from exc
    if (
        payload.get("plan_run_id") != plan_run_id
        or str(payload.get("platform") or "").lower() != platform
        or payload.get("version_no") != version_no
    ):
        raise JiraDeliveryError(
            409, "delivery export identity mismatches bound PlanRun/platform/version",
        )
    return payload


def _resolve_verified_evidence_dir(
    *,
    pub_rel_path: Optional[str],
    root_plan_run_id: int,
    serial: Optional[str],
    event_dir: Optional[str],
) -> Optional[str]:
    if not pub_rel_path:
        return None
    try:
        path = resolve_evidence_event_path(
            pub_rel_path,
            root_plan_run_id=root_plan_run_id,
            serial=serial,
            event_dir=event_dir,
            must_exist=True,
        )
        return str(path)
    except ArtifactPathError:
        return None


def _build_manifest_and_xls_rows(
    db: Session,
    *,
    plan_run: PlanRun,
    platform: str,
    delivery_version: DeliveryVersion,
    compat_rerun: Optional[CompatRerun] = None,
    compat_event_paths: Optional[Mapping[str, str]] = None,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    root_plan_run_id = int(plan_run.root_plan_run_id or plan_run.id)
    scoped_issue_ids = [int(i) for i in (delivery_version.issue_ids or [])]
    scoped_event_ids = [str(e) for e in (delivery_version.event_ids or []) if e]

    nfs_root = resolve_shared_storage_root()
    export_payload = _load_delivery_export_if_present(
        nfs_root=nfs_root,
        plan_run_id=plan_run.id,
        platform=platform,
        version_no=delivery_version.version_no,
    )

    if (
        export_payload
        and isinstance(export_payload.get("issues"), list)
        and (export_payload["issues"] or not scoped_issue_ids)
    ):
        raw_issues = [
            item for item in export_payload["issues"]
            if int(item.get("signature_id", -1)) in set(scoped_issue_ids)
        ]
    else:
        raw_issues = [
            item for item in facts.list_partition_delivery_issues(
                db, plan_run_id=plan_run.id, platform=platform,
            )
            if int(item["signature_id"]) in set(scoped_issue_ids)
        ]

    # Collect occurrences strictly within registered issue_ids + event_ids.
    occ_dle_pairs = facts.list_partition_occurrences_for_scope(
        db,
        plan_run_id=plan_run.id,
        platform=platform,
        signature_ids=scoped_issue_ids,
        event_ids=scoped_event_ids,
    )
    pubs_by_dle = {
        str(p.dle_id): p
        for p in facts.list_partition_evidence_publications(
            db, plan_run_id=plan_run.id, platform=platform,
        )
        if p.state == facts.PUBLICATION_STATE_VERIFIED
    }

    occ_by_sig: dict[int, list[dict[str, Any]]] = {}
    candidates: list[dict[str, Any]] = []
    seen_candidates: set[tuple[int, int]] = set()
    for occ, dle in occ_dle_pairs:
        dle_id_str = str(dle.id)
        item = {
            "signature_id": int(occ.signature_id),
            "occurrence_id": int(occ.id),
            "occurrence_key": occ.occurrence_key,
            "dle_id": dle_id_str,
            "serial": occ.serial,
            "observed_at": occ.observed_at.isoformat() if occ.observed_at else "",
            "build_version": occ.build_version or "",
        }
        occ_by_sig.setdefault(int(occ.signature_id), []).append(item)
        cand_key = (int(occ.signature_id), int(occ.id))
        if cand_key not in seen_candidates:
            seen_candidates.add(cand_key)
            candidates.append({
                "signature_id": int(occ.signature_id),
                "occurrence_id": int(occ.id),
                "occurrence_key": occ.occurrence_key,
                "dle_id": dle_id_str,
                "serial": occ.serial,
            })

    issues_manifest: list[dict[str, Any]] = []
    xls_rows: list[dict[str, Any]] = []
    for issue in raw_issues:
        sig_id = int(issue["signature_id"])
        rep_dle_id = issue.get("representative_dle_id")
        verified_path: Optional[str] = None
        if compat_event_paths is not None and rep_dle_id:
            verified_path = compat_event_paths.get(str(rep_dle_id))
        if verified_path is None and rep_dle_id and str(rep_dle_id) in pubs_by_dle:
            pub = pubs_by_dle[str(rep_dle_id)]
            verified_path = _resolve_verified_evidence_dir(
                pub_rel_path=pub.destination_rel_path,
                root_plan_run_id=root_plan_run_id,
                serial=None,
                event_dir=None,
            )

        sig_occs = occ_by_sig.get(sig_id, [])
        serials = sorted({o["serial"] for o in sig_occs if o.get("serial")})
        exp_times = sorted(o["observed_at"] for o in sig_occs if o.get("observed_at"))
        build_versions = list(issue.get("build_versions") or [])
        sig_raw = str(issue.get("signature_raw") or "")
        norm_key = str(issue.get("normalized_key") or "")
        event_type = str(issue.get("event_type") or "")

        issues_manifest.append({
            "signature_id": sig_id,
            "signature_raw": sig_raw,
            "normalized_key": norm_key,
            "event_type": event_type,
            "occurrences_count": int(issue.get("occurrences") or len(sig_occs)),
            "machines_count": int(issue.get("machines") or len(serials)),
            "serials": serials,
            "build_versions": build_versions,
            "representative_dle_id": rep_dle_id,
            "representative_verified_path": verified_path,
            "occurrence_ids": sorted({int(o["occurrence_id"]) for o in sig_occs}),
            "dle_ids": sorted({o["dle_id"] for o in sig_occs}),
        })

        xls_rows.append({
            "Id": str(sig_id),
            "Path": verified_path or "",
            "Version": ", ".join(build_versions),
            "ExpTime": exp_times[-1] if exp_times else "",
            "ExpClass": event_type,
            "ExpType ": norm_key,
            "CurProcess": sig_raw,
            "Package": "",
            "Detail": sig_raw,
            "CausedBy": "",
            "Count": int(issue.get("occurrences") or len(sig_occs)),
            "DeviceId": ",".join(serials),
            "Device Count": int(issue.get("machines") or len(serials)),
        })

    manifest: dict[str, Any] = {
        "schema": "stp.jira_delivery_input/v1",
        "plan_run_id": int(plan_run.id),
        "root_plan_run_id": root_plan_run_id,
        "platform": platform,
        "delivery_version_id": int(delivery_version.id),
        "delivery_version_no": int(delivery_version.version_no),
        "compat_rerun_id": int(compat_rerun.id) if compat_rerun is not None else None,
        "issue_ids": scoped_issue_ids,
        "event_ids": scoped_event_ids,
        "issues": issues_manifest,
        "attribution_candidates": sorted(
            candidates, key=lambda c: (c["signature_id"], c["occurrence_id"]),
        ),
    }
    return manifest, tuple(xls_rows)


_IDENTITY_ID_HEADERS = ("id", "signature_id", "signature id")
_IDENTITY_KEY_HEADERS = ("exptype", "exp_type", "norm_key", "normalized_key", "normalized key")
_IDENTITY_RAW_HEADERS = ("detail", "curprocess", "signature_raw", "signature raw")
_IDENTITY_PATH_HEADERS = ("path", "representative_verified_path", "representative verified path")


def _coerce_signature_id(value: object) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _read_uploaded_workbook_rows(payload: bytes) -> list[tuple[str, int, dict[str, str]]]:
    """读取上传表格的非空数据行（保留 sheet 名与行号用于拒绝时定位）。"""
    try:
        book = xlrd.open_workbook(file_contents=payload, on_demand=True)
    except Exception as exc:  # xlrd 对损坏/非 Excel 载荷抛多种异常
        raise JiraDeliveryError(400, f"uploaded file is not a readable Excel workbook: {exc}") from exc
    rows: list[tuple[str, int, dict[str, str]]] = []
    try:
        for sheet in book.sheets():
            if sheet.nrows < 1:
                continue
            headers = [str(sheet.cell_value(0, c)).strip().lower() for c in range(sheet.ncols)]
            if not any(headers):
                continue
            for row_idx in range(1, sheet.nrows):
                values = [str(sheet.cell_value(row_idx, c)).strip() for c in range(sheet.ncols)]
                if not any(values):
                    continue
                rows.append((sheet.name, row_idx + 1, dict(zip(headers, values, strict=False))))
    finally:
        book.release_resources()
    return rows


def validate_uploaded_jira_workbook_scope(
    *,
    manifest: Mapping[str, Any],
    uploaded_bytes: bytes,
) -> None:
    """证明上传表格属于登记交付范围：每一行的身份列都必须命中登记问题。

    ``delivery_version`` / ``compat_rerun`` 入口允许附带表格，但附带表格不能成为
    绕过登记范围的后门。只有整张表可证明属于该登记范围时才放行，否则拒绝请求，
    子进程不会启动；``source=upload`` 的独立手工上传入口不受此约束。
    """
    registered_ids: set[int] = set()
    registered_keys: set[str] = set()
    registered_raws: set[str] = set()
    registered_paths: set[str] = set()
    for item in manifest.get("issue_ids") or []:
        sig_id = _coerce_signature_id(item)
        if sig_id is not None:
            registered_ids.add(sig_id)
    for item in manifest.get("issues") or []:
        if not isinstance(item, Mapping):
            continue
        sig_id = _coerce_signature_id(item.get("signature_id"))
        if sig_id is not None:
            registered_ids.add(sig_id)
        for value, bucket in (
            (item.get("normalized_key"), registered_keys),
            (item.get("signature_raw"), registered_raws),
            (item.get("representative_verified_path"), registered_paths),
        ):
            text = str(value or "").strip()
            if text:
                bucket.add(text)

    if not registered_ids and not registered_keys and not registered_raws and not registered_paths:
        raise JiraDeliveryError(
            409,
            "registered delivery scope contains no issues; an attached table cannot be proven in scope",
        )

    rows = _read_uploaded_workbook_rows(uploaded_bytes)
    if not rows:
        raise JiraDeliveryError(
            409, "uploaded table has no data rows to prove registered delivery scope",
        )

    def _matched(row: Mapping[str, str], headers: tuple[str, ...], bucket: set) -> bool:
        return any(row.get(header) and row[header] in bucket for header in headers)

    rejected: list[str] = []
    for sheet_name, row_no, row in rows:
        id_raw = next((row[h] for h in _IDENTITY_ID_HEADERS if row.get(h)), "")
        key_raw = next((row[h] for h in _IDENTITY_KEY_HEADERS if row.get(h)), "")
        id_ok = _coerce_signature_id(id_raw) in registered_ids if id_raw else False
        key_ok = bool(key_raw) and key_raw in registered_keys
        other_ok = _matched(row, _IDENTITY_RAW_HEADERS, registered_raws) or _matched(
            row, _IDENTITY_PATH_HEADERS, registered_paths,
        )
        if id_raw and not id_ok:
            rejected.append(f"{sheet_name}!{row_no} id={id_raw}")
        elif key_raw and not key_ok:
            rejected.append(f"{sheet_name}!{row_no} normalized_key={key_raw}")
        elif not id_ok and not key_ok and not other_ok:
            rejected.append(f"{sheet_name}!{row_no} has no registered identity value")
    if rejected:
        detail = "; ".join(rejected[:5])
        suffix = "" if len(rejected) <= 5 else f" (+{len(rejected) - 5} more)"
        raise JiraDeliveryError(
            409,
            f"uploaded table contains rows outside registered delivery scope: {detail}{suffix}",
        )


def resolve_delivery_jira_input(
    db: Session,
    *,
    plan_run_id: Optional[int],
    platform: Optional[str],
    delivery_version_no: Optional[int],
    delivery_version_id: Optional[int] = None,
    stage: str,
    uploaded_bytes: Optional[bytes] = None,
    uploaded_filename: Optional[str] = None,
) -> ResolvedJiraDeliveryInput:
    """解析并严格校验证明属于指定 ``PlanRun × platform × DeliveryVersion`` 的提单输入。"""
    if plan_run_id is None:
        raise JiraDeliveryError(400, "plan_run_id is required for source=delivery_version")
    plat = _normalize_platform(platform)
    plan_run = db.get(PlanRun, plan_run_id)
    if plan_run is None:
        raise JiraDeliveryError(404, f"plan run {plan_run_id} not found")

    partition = facts.get_partition_mode(db, plan_run_id=plan_run_id, platform=plat)
    if partition is None or partition.mode != facts.PARTITION_MODE_DEDUP_FIRST:
        raise JiraDeliveryError(
            409,
            "source=delivery_version requires a DEDUP_FIRST partition",
        )

    if delivery_version_id is not None:
        version = facts.get_delivery_version_by_id(db, version_id=delivery_version_id)
        if version is None:
            raise JiraDeliveryError(404, f"delivery version {delivery_version_id} not found")
        if version.plan_run_id != plan_run_id or version.platform.lower() != plat:
            raise JiraDeliveryError(
                409,
                "delivery_version_id does not belong to target plan_run_id and platform",
            )
        if delivery_version_no is not None and version.version_no != delivery_version_no:
            raise JiraDeliveryError(
                409,
                "delivery_version_id mismatches delivery_version_no",
            )
    elif delivery_version_no is not None:
        version = facts.get_delivery_version(
            db, plan_run_id=plan_run_id, platform=plat, version_no=delivery_version_no,
        )
        if version is None:
            raise JiraDeliveryError(
                404,
                f"delivery version v{delivery_version_no} not found for plan_run={plan_run_id} platform={plat}",
            )
    else:
        raise JiraDeliveryError(
            400, "delivery_version_no or delivery_version_id is required for source=delivery_version",
        )

    if not version.issue_ids and stage == "upload_list":
        raise JiraDeliveryError(
            409, "delivery version contains no registered issues for upload_list",
        )

    manifest, xls_rows = _build_manifest_and_xls_rows(
        db, plan_run=plan_run, platform=plat, delivery_version=version,
    )

    uploaded_suffix: Optional[str] = None
    if uploaded_bytes is not None:
        suffix = Path(uploaded_filename or "").suffix.lower()
        if suffix not in {".xls", ".xlsx"}:
            raise JiraDeliveryError(400, "uploaded file must be .xls/.xlsx")
        uploaded_suffix = suffix
        validate_uploaded_jira_workbook_scope(
            manifest=manifest, uploaded_bytes=uploaded_bytes,
        )

    label = f"delivery:{plan_run.id}:{plat}:v{version.version_no}"
    return ResolvedJiraDeliveryInput(
        source=SOURCE_DELIVERY_VERSION,
        plan_run_id=int(plan_run.id),
        root_plan_run_id=int(plan_run.root_plan_run_id or plan_run.id),
        platform=plat,
        delivery_version_id=int(version.id),
        delivery_version_no=int(version.version_no),
        compat_rerun_id=None,
        input_source_label=label,
        manifest=manifest,
        xls_rows=xls_rows,
        uploaded_bytes=uploaded_bytes,
        uploaded_suffix=uploaded_suffix,
    )


def resolve_compat_rerun_jira_input(
    db: Session,
    *,
    plan_run_id: Optional[int],
    platform: Optional[str],
    compat_rerun_id: Optional[int],
    stage: str,
    uploaded_bytes: Optional[bytes] = None,
    uploaded_filename: Optional[str] = None,
) -> ResolvedJiraDeliveryInput:
    """绑定受控兼容补跑的提单输入，并拒绝跨区或重复建单消费。"""
    if plan_run_id is None:
        raise JiraDeliveryError(400, "plan_run_id is required for source=compat_rerun")
    if compat_rerun_id is None:
        raise JiraDeliveryError(400, "compat_rerun_id is required for source=compat_rerun")
    plat = _normalize_platform(platform)

    plan_run = db.get(PlanRun, plan_run_id)
    if plan_run is None:
        raise JiraDeliveryError(404, f"plan run {plan_run_id} not found")

    rerun = facts.get_compat_rerun(db, rerun_id=compat_rerun_id)
    if rerun is None:
        raise JiraDeliveryError(404, f"compat rerun {compat_rerun_id} not found")
    if rerun.plan_run_id != plan_run_id or rerun.platform.lower() != plat:
        raise JiraDeliveryError(
            409, "compat_rerun_id does not belong to target plan_run_id and platform",
        )
    if rerun.status != facts.COMPAT_RERUN_STATUS_COMPLETED:
        raise JiraDeliveryError(
            409, f"compat rerun {compat_rerun_id} is in status={rerun.status}, expected COMPLETED",
        )

    existing_create = db.execute(
        select(JiraRun).where(
            JiraRun.compat_rerun_id == rerun.id,
            JiraRun.stage == stage,
            JiraRun.status.in_(sorted(_TERMINAL_CREATE_ACTIVE)),
        )
    ).scalar_one_or_none()
    if existing_create is not None and stage == "create":
        raise JiraDeliveryError(
            409,
            f"compat rerun {rerun.id} already has a {existing_create.status} Jira create run ({existing_create.console_run_id})",
        )

    merge_xls = Path(rerun.merge_xls_path) if rerun.merge_xls_path else None
    if merge_xls is None or not merge_xls.is_file() or merge_xls.is_symlink():
        raise JiraDeliveryError(409, "compat rerun merge_xls artifact is missing")

    compat_event_paths: dict[str, str] = {}
    for rec in rerun.source_records or []:
        if rec.get("dle_id") and rec.get("copied_path"):
            compat_event_paths[str(rec["dle_id"])] = str(rec["copied_path"])

    version: Optional[DeliveryVersion] = None
    if rerun.source_delivery_version_id is not None:
        version = facts.get_delivery_version_by_id(
            db, version_id=rerun.source_delivery_version_id,
        )
    if version is not None:
        manifest, xls_rows = _build_manifest_and_xls_rows(
            db,
            plan_run=plan_run,
            platform=plat,
            delivery_version=version,
            compat_rerun=rerun,
            compat_event_paths=compat_event_paths,
        )
    else:
        manifest = {
            "schema": "stp.jira_delivery_input/v1",
            "plan_run_id": int(plan_run.id),
            "root_plan_run_id": int(plan_run.root_plan_run_id or plan_run.id),
            "platform": plat,
            "delivery_version_id": None,
            "delivery_version_no": None,
            "compat_rerun_id": int(rerun.id),
            "issue_ids": [],
            "event_ids": [],
            "issues": [],
            "attribution_candidates": [],
        }
        xls_rows = ()

    uploaded_suffix: Optional[str] = None
    if uploaded_bytes is not None:
        suffix = Path(uploaded_filename or "").suffix.lower()
        if suffix not in {".xls", ".xlsx"}:
            raise JiraDeliveryError(400, "uploaded file must be .xls/.xlsx")
        uploaded_suffix = suffix
        validate_uploaded_jira_workbook_scope(
            manifest=manifest, uploaded_bytes=uploaded_bytes,
        )

    label = f"compat_rerun:{plan_run.id}:{plat}:r{rerun.id}"
    return ResolvedJiraDeliveryInput(
        source=SOURCE_COMPAT_RERUN,
        plan_run_id=int(plan_run.id),
        root_plan_run_id=int(plan_run.root_plan_run_id or plan_run.id),
        platform=plat,
        delivery_version_id=int(version.id) if version is not None else None,
        delivery_version_no=int(version.version_no) if version is not None else None,
        compat_rerun_id=int(rerun.id),
        input_source_label=label,
        manifest=manifest,
        xls_rows=xls_rows,
        source_xls_path=merge_xls if uploaded_bytes is None else None,
        uploaded_bytes=uploaded_bytes,
        uploaded_suffix=uploaded_suffix,
    )


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


def prepare_isolated_jira_workspace(
    *,
    work_root: Path,
    console_run_id: str,
    resolved_input: ResolvedJiraDeliveryInput,
    stage: str,
) -> PreparedJiraWorkspace:
    """为每次 Jira run 准备独立、可写的隔离工作目录（不写入 evidence/devices 主目录）。"""
    resolved_work_root = work_root.resolve(strict=False)
    ws_root = (resolved_work_root / "jira_workspaces" / console_run_id).resolve(strict=False)
    if not ws_root.is_relative_to(resolved_work_root):
        raise JiraDeliveryError(400, "invalid jira workspace path")
    ws_root.mkdir(parents=True, exist_ok=True)

    manifest_path = ws_root / "manifest.json"
    _atomic_write_json(manifest_path, resolved_input.manifest)

    if resolved_input.uploaded_bytes is not None:
        ext = resolved_input.uploaded_suffix or ".xlsx"
        input_xls = ws_root / f"uploaded_input_{uuid.uuid4().hex[:8]}{ext}"
        input_xls.write_bytes(resolved_input.uploaded_bytes)
    elif resolved_input.source_xls_path is not None:
        input_xls = ws_root / resolved_input.source_xls_path.name
        shutil.copy2(resolved_input.source_xls_path, input_xls)
    else:
        xls_name = (
            f"delivery_{resolved_input.plan_run_id}_{resolved_input.platform}_"
            f"v{resolved_input.delivery_version_no or 0}_{stage}.xls"
        )
        input_xls = write_dedup_xls(ws_root / xls_name, resolved_input.xls_rows)

    return PreparedJiraWorkspace(
        workspace_dir=ws_root,
        input_xls=input_xls,
        manifest_path=manifest_path,
    )


def _extract_vendor_result_artifacts(
    workspace_dir: Optional[str],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """从隔离工作区读取可验证归因输出与（若有）厂商 result JSON 列表。"""
    if not workspace_dir:
        return [], [], []
    ws = Path(workspace_dir).resolve(strict=False)
    if not ws.is_dir():
        return [], [], []

    structured_links: list[dict[str, Any]] = []
    for attr_name in ("attribution.json", "jira_attribution.json"):
        attr_file = ws / attr_name
        if attr_file.is_file() and not attr_file.is_symlink():
            try:
                data = json.loads(attr_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items = data.get("links") if isinstance(data, Mapping) else data
            if isinstance(items, list):
                for entry in items:
                    if isinstance(entry, Mapping):
                        structured_links.append(dict(entry))

    result_keys: list[str] = []
    result_rows: list[dict[str, Any]] = []
    result_dir = ws / "result"
    if result_dir.is_dir() and not result_dir.is_symlink():
        for json_file in sorted(result_dir.glob("*_jira_batch_create_result_*.json")):
            if json_file.is_symlink():
                continue
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows = payload if isinstance(payload, list) else payload.get("results", [])
            if not isinstance(rows, list):
                continue
            for item in rows:
                if not isinstance(item, Mapping):
                    continue
                result_rows.append(dict(item))
                for key_field in ("issue_key", "matched_jira_key"):
                    raw_key = str(item.get(key_field) or "").strip()
                    if _ISSUE_KEY_EXACT_RE.match(raw_key) and raw_key not in result_keys:
                        result_keys.append(raw_key)
                if item.get("occurrence_id") and item.get("signature_id") and (
                    item.get("issue_key") or item.get("matched_jira_key")
                ):
                    structured_links.append({
                        "issue_key": item.get("issue_key") or item.get("matched_jira_key"),
                        "occurrence_id": item.get("occurrence_id"),
                        "signature_id": item.get("signature_id"),
                        "evidence_source": "vendor_result_json",
                    })

    return structured_links, result_keys, result_rows


def _upsert_jira_occurrence_links(
    db: Session,
    *,
    jira_run: JiraRun,
    verified_links: Sequence[Mapping[str, Any]],
) -> None:
    if not verified_links:
        return
    dialect = db.bind.dialect.name if db.bind is not None else ""
    values = [
        {
            "jira_run_id": int(jira_run.id),
            "plan_run_id": int(jira_run.plan_run_id),
            "platform": str(jira_run.platform),
            "delivery_version_id": jira_run.delivery_version_id,
            "delivery_version_no": jira_run.delivery_version_no,
            "signature_id": int(item["signature_id"]),
            "occurrence_id": int(item["occurrence_id"]),
            "issue_key": str(item["issue_key"]),
            "evidence_source": str(item["evidence_source"]),
        }
        for item in verified_links
    ]
    if dialect == "postgresql":
        stmt = pg_insert(JiraOccurrenceLink).values(values).on_conflict_do_nothing(
            constraint="uq_jira_occurrence_link_run_occ_key",
        )
        db.execute(stmt)
        return
    if dialect == "sqlite":
        stmt = sqlite_insert(JiraOccurrenceLink).values(values).on_conflict_do_nothing(
            index_elements=["jira_run_id", "occurrence_id", "issue_key"],
        )
        db.execute(stmt)
        return
    for val in values:
        exists = db.execute(
            select(JiraOccurrenceLink.id).where(
                JiraOccurrenceLink.jira_run_id == val["jira_run_id"],
                JiraOccurrenceLink.occurrence_id == val["occurrence_id"],
                JiraOccurrenceLink.issue_key == val["issue_key"],
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(JiraOccurrenceLink(**val))


def list_jira_occurrence_links(
    db: Session, *, jira_run_ids: Sequence[int],
) -> dict[int, list[JiraOccurrenceLink]]:
    """按 ``jira_run_id`` 批量查询已验证的 issue_key ↔ 发生关联。"""
    ids = [int(i) for i in jira_run_ids]
    if not ids:
        return {}
    rows = db.execute(
        select(JiraOccurrenceLink)
        .where(JiraOccurrenceLink.jira_run_id.in_(ids))
        .order_by(JiraOccurrenceLink.jira_run_id, JiraOccurrenceLink.id)
    ).scalars()
    grouped: dict[int, list[JiraOccurrenceLink]] = {}
    for row in rows:
        grouped.setdefault(int(row.jira_run_id), []).append(row)
    return grouped


def _dedupe_preserve_order(keys: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def record_jira_run_completion(
    db: Session,
    *,
    jira_run: JiraRun,
    status: str,
    exit_code: Optional[int],
    ended_at: Optional[datetime],
    error: Optional[str],
    log_lines: Iterable[str],
) -> bool:
    """幂等回写 JiraRun 终态，并在存在可验证证据时登记发生关联与签名 key。

    若同一 ``console_run_id`` 已处于终态（例如重复触发回调），保持原终态并返回 ``False``。
    """
    if jira_run.status in _SAFE_STAGE_TERMINAL:
        return False

    stdout_keys = parse_issue_keys(log_lines)
    structured_links, result_keys, result_rows = _extract_vendor_result_artifacts(
        jira_run.workspace_dir,
    )
    all_keys = _dedupe_preserve_order([*stdout_keys, *result_keys])

    jira_run.status = status or jira_run.status
    jira_run.exit_code = exit_code
    jira_run.ended_at = ended_at
    jira_run.error = (error or "")[:1024] or None
    if all_keys:
        jira_run.issue_keys = all_keys

    is_bound = jira_run.delivery_version_id is not None or jira_run.compat_rerun_id is not None
    if not is_bound or not jira_run.workspace_dir:
        db.flush()
        return True

    manifest_file = Path(jira_run.workspace_dir) / "manifest.json"
    allowed_candidates: dict[tuple[int, int], dict[str, Any]] = {}
    if manifest_file.is_file() and not manifest_file.is_symlink():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            for cand in manifest.get("attribution_candidates", []):
                allowed_candidates[(int(cand["signature_id"]), int(cand["occurrence_id"]))] = cand
        except (OSError, ValueError):
            manifest = {}

    verified_links: list[dict[str, Any]] = []
    attributed_keys: set[str] = set()
    gaps: list[dict[str, Any]] = []
    seen_link_keys: set[tuple[int, str]] = set()

    for item in structured_links:
        issue_key = str(item.get("issue_key") or "").strip()
        raw_occ = item.get("occurrence_id")
        raw_sig = item.get("signature_id")
        if not issue_key or not _ISSUE_KEY_EXACT_RE.match(issue_key):
            continue
        if raw_occ is None or raw_sig is None:
            gaps.append({
                "kind": "incomplete_attribution_evidence",
                "issue_key": issue_key,
                "reason": "missing_signature_or_occurrence_id",
            })
            continue
        occ_id = int(raw_occ)
        sig_id = int(raw_sig)
        if (sig_id, occ_id) not in allowed_candidates:
            gaps.append({
                "kind": "unverified_link_scope",
                "issue_key": issue_key,
                "signature_id": sig_id,
                "occurrence_id": occ_id,
                "reason": "occurrence_not_in_bound_delivery_version",
            })
            continue
        if (occ_id, issue_key) not in seen_link_keys:
            seen_link_keys.add((occ_id, issue_key))
            verified_links.append({
                "issue_key": issue_key,
                "signature_id": sig_id,
                "occurrence_id": occ_id,
                "evidence_source": str(item.get("evidence_source") or "vendor_attribution_json"),
            })
        attributed_keys.add(issue_key)

    has_vendor_json_without_occ = any(
        (r.get("issue_key") or r.get("matched_jira_key")) and not r.get("occurrence_id")
        for r in result_rows
    )
    missing_reason = (
        "vendor_result_json_lacks_occurrence_id"
        if has_vendor_json_without_occ
        else "missing_occurrence_evidence_in_vendor_output"
    )
    for key in all_keys:
        if key not in attributed_keys:
            gaps.append({
                "kind": "unattributed_issue_key",
                "issue_key": key,
                "reason": missing_reason,
            })

    _upsert_jira_occurrence_links(db, jira_run=jira_run, verified_links=verified_links)
    keys_by_sig: dict[int, list[str]] = {}
    for link in verified_links:
        keys_by_sig.setdefault(int(link["signature_id"]), []).append(str(link["issue_key"]))
    for sig_id, sig_keys in keys_by_sig.items():
        facts.append_issue_signature_keys(
            db, signature_id=sig_id, issue_keys=_dedupe_preserve_order(sig_keys),
        )

    if jira_run.stage != "create" or not all_keys:
        jira_run.attribution_status = ATTRIBUTION_NOT_APPLICABLE
    elif verified_links and not gaps:
        jira_run.attribution_status = ATTRIBUTION_VERIFIED
    elif verified_links:
        jira_run.attribution_status = ATTRIBUTION_PARTIAL
    else:
        jira_run.attribution_status = ATTRIBUTION_UNATTRIBUTED
    jira_run.attribution_gaps = gaps

    if jira_run.compat_rerun_id is not None:
        rerun = facts.get_compat_rerun(db, rerun_id=jira_run.compat_rerun_id)
        if rerun is not None and jira_run.status == "SUCCESS":
            facts.update_compat_rerun(
                db,
                row=rerun,
                status=rerun.status,
                jira_run_id=int(jira_run.id),
                error=rerun.error,
            )

    db.flush()
    return True
