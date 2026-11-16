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
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import openpyxl
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


@dataclass(frozen=True)
class _WorkbookSheet:
    """上传工作簿的一个 sheet：原始表头与非空数据行（保留物理行号）。"""

    name: str
    headers: tuple[str, ...]
    rows: tuple[tuple[int, tuple[str, ...]], ...]


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == value and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _header_text(value: Any) -> str:
    """表头保留原始文本（含 ``ExpType `` 的尾随空格），只把空值归一为空串。"""
    if value is None:
        return ""
    if isinstance(value, float) and value == value and value.is_integer():
        return str(int(value))
    return str(value)


def _coerce_signature_id(value: object) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _read_workbook_sheets(payload: bytes, suffix: str) -> list[_WorkbookSheet]:
    """读取 .xls（xlrd）或 .xlsx（openpyxl），保留原始表头文本。"""
    sheets: list[_WorkbookSheet] = []
    if suffix == ".xlsx":
        try:
            book = openpyxl.load_workbook(BytesIO(payload), read_only=True, data_only=True)
        except Exception as exc:
            raise JiraDeliveryError(400, f"uploaded file is not a readable Excel workbook: {exc}") from exc
        try:
            for sheet in book.worksheets:
                rows = list(sheet.iter_rows(values_only=True))
                if not rows:
                    continue
                headers = tuple(_header_text(value) for value in rows[0])
                if not any(header.strip() for header in headers):
                    continue
                data: list[tuple[int, tuple[str, ...]]] = []
                for row_no, values in enumerate(rows[1:], start=2):
                    cells = tuple(_cell_text(value) for value in values)
                    if not any(cells):
                        continue
                    data.append((row_no, cells))
                sheets.append(_WorkbookSheet(name=sheet.title, headers=headers, rows=tuple(data)))
        finally:
            book.close()
        return sheets

    try:
        book = xlrd.open_workbook(file_contents=payload, on_demand=True)
    except Exception as exc:  # xlrd 对损坏/非 Excel 载荷抛多种异常
        raise JiraDeliveryError(400, f"uploaded file is not a readable Excel workbook: {exc}") from exc
    try:
        for sheet in book.sheets():
            if sheet.nrows < 1:
                continue
            headers = tuple(_header_text(sheet.cell_value(0, c)) for c in range(sheet.ncols))
            if not any(header.strip() for header in headers):
                continue
            data = []
            for row_idx in range(1, sheet.nrows):
                cells = tuple(_cell_text(sheet.cell_value(row_idx, c)) for c in range(sheet.ncols))
                if not any(cells):
                    continue
                data.append((row_idx + 1, cells))
            sheets.append(_WorkbookSheet(name=sheet.name, headers=headers, rows=tuple(data)))
    finally:
        book.release_resources()
    return sheets


_DEDUP_SIGNATURE_HEADERS = ("Id", "Signature ID")
_DEDUP_KEY_HEADERS = ("ExpType ",)
_DEDUP_RAW_HEADERS = ("Detail", "CurProcess")
_DEDUP_PATH_HEADERS = ("Path",)

_CREATE_SIGNATURE_ALIASES = frozenset({"signature id", "signature_id", "signatureid", "id"})
_CREATE_OCCURRENCE_ALIASES = frozenset({"occurrence ids", "occurrence_ids", "occurrenceids"})
_CREATE_OCCURRENCE_KEY_ALIASES = frozenset({"occurrence keys", "occurrence_keys", "occurrencekeys"})


def _header_indexes(sheet: _WorkbookSheet, schema: str) -> dict[str, int | list[int]]:
    """校验表头冲突并返回实际消费字段的列号。

    重复列、大小写冲突与同一字段的别名冲突在启动前拒绝；返回的列号就是工具
    实际消费的列，避免平台校验的字段与被工具读取的字段不一致。
    """
    normalized = [header.strip().casefold() for header in sheet.headers]
    seen: dict[str, int] = {}
    for idx, name in enumerate(normalized):
        if not name:
            continue
        if name in seen:
            raise JiraDeliveryError(
                409,
                f"uploaded workbook header conflict in sheet {sheet.name!r}: "
                f"duplicate column {sheet.headers[idx]!r} (case-insensitive)",
            )
        seen[name] = idx

    if schema == "dedup":
        def _pick(group: tuple[str, ...], label: str) -> Optional[int]:
            found = [idx for idx, header in enumerate(sheet.headers) if header in group]
            if len(found) > 1:
                names = [sheet.headers[idx] for idx in found]
                raise JiraDeliveryError(
                    409,
                    f"uploaded workbook header conflict in sheet {sheet.name!r}: "
                    f"multiple {label} columns {names}",
                )
            return found[0] if found else None

        raw_indexes = [
            idx for idx, header in enumerate(sheet.headers) if header in _DEDUP_RAW_HEADERS
        ]
        return {
            "signature": _pick(_DEDUP_SIGNATURE_HEADERS, "signature"),
            "key": _pick(_DEDUP_KEY_HEADERS, "normalized key"),
            "raw": raw_indexes,
            "path": _pick(_DEDUP_PATH_HEADERS, "evidence path"),
        }

    def _alias_pick(aliases: frozenset[str], label: str, *, required: bool) -> Optional[int]:
        found = [idx for idx, name in enumerate(normalized) if name in aliases]
        if len(found) > 1:
            names = [sheet.headers[idx] for idx in found]
            raise JiraDeliveryError(
                409,
                f"uploaded workbook header conflict in sheet {sheet.name!r}: "
                f"multiple {label} aliases {names}",
            )
        if required and not found:
            raise JiraDeliveryError(
                409,
                f"uploaded workbook sheet {sheet.name!r} is missing the {label} column",
            )
        return found[0] if found else None

    return {
        "signature": _alias_pick(_CREATE_SIGNATURE_ALIASES, "Signature ID", required=True),
        "occurrences": _alias_pick(_CREATE_OCCURRENCE_ALIASES, "Occurrence IDs", required=False),
        "occurrence_keys": _alias_pick(_CREATE_OCCURRENCE_KEY_ALIASES, "Occurrence Keys", required=False),
    }


def _row_cell(cells: Sequence[str], col: Optional[int]) -> str:
    return cells[int(col)] if col is not None and int(col) < len(cells) else ""


def _parse_occurrence_ids(value: str) -> tuple[list[int], list[str]]:
    """按厂商契约解析 Occurrence IDs：JSON 数组或逗号/分号分隔。"""
    text = str(value or "").strip()
    if not text:
        return [], []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            raw_items = parsed
        else:
            raw_items = [item for item in text.replace(";", ",").split(",") if item.strip()]
    else:
        raw_items = [item for item in text.replace(";", ",").split(",") if item.strip()]
    valid: list[int] = []
    invalid: list[str] = []
    for item in raw_items:
        parsed = _coerce_signature_id(item)
        if parsed is None:
            invalid.append(str(item).strip())
        else:
            valid.append(parsed)
    return valid, invalid


def validate_uploaded_jira_workbook_scope(
    *,
    manifest: Mapping[str, Any],
    uploaded_bytes: bytes,
    uploaded_suffix: Optional[str],
    stage: str,
) -> None:
    """证明上传表格属于登记交付范围，并拒绝列/别名冲突。

    ``delivery_version`` / ``compat_rerun`` 入口允许附带表格，但附带表格不能成为
    绕过登记范围的后门。校验按工具实际消费的字段执行：

    - ``upload_list``（去重 .xls）：``Id`` / ``Signature ID``、``ExpType ``、``Path``、
      ``Detail`` / ``CurProcess``；同一行已填字段必须指向同一条登记问题。
    - ``create``（厂商清单 .xlsx）：``Signature ID`` 与 ``Occurrence IDs``；签名必须
      在登记范围，发生必须命中该签名的 ``attribution_candidates``。

    重复列、大小写冲突、同一字段的别名冲突、范围外签名/发生均在启动子进程前返回
    409；``source=upload`` 的独立手工上传入口不受此约束。
    """
    if not uploaded_suffix or uploaded_suffix.lower() not in {".xls", ".xlsx"}:
        raise JiraDeliveryError(400, "uploaded file must be .xls/.xlsx")
    suffix = uploaded_suffix.lower()
    if stage == "create" and suffix != ".xlsx":
        # 阶段二厂商工具消费的是阶段一生成的 .xlsx 上传清单；去重 .xls 没有
        # Summary/Occurrence 等列，交给工具只会失败，绑定入口提前拒绝。
        raise JiraDeliveryError(
            400,
            "stage=create attached table must be the vendor-generated .xlsx upload list",
        )
    sheets = _read_workbook_sheets(uploaded_bytes, suffix)
    if not sheets or not any(sheet.rows for sheet in sheets):
        raise JiraDeliveryError(
            409, "uploaded table has no data rows to prove registered delivery scope",
        )

    if stage == "create":
        registered_ids = {
            sig_id for item in (manifest.get("issues") or [])
            if isinstance(item, Mapping)
            and (sig_id := _coerce_signature_id(item.get("signature_id"))) is not None
        }
        candidates_by_sig: dict[int, set[int]] = {}
        for item in manifest.get("attribution_candidates") or []:
            if not isinstance(item, Mapping):
                continue
            sig_id = _coerce_signature_id(item.get("signature_id"))
            occ_id = _coerce_signature_id(item.get("occurrence_id"))
            if sig_id is None or occ_id is None:
                continue
            candidates_by_sig.setdefault(sig_id, set()).add(occ_id)
        if not registered_ids:
            raise JiraDeliveryError(
                409,
                "registered delivery scope contains no issues; an attached table cannot be proven in scope",
            )
        rejected: list[str] = []
        for sheet in sheets:
            columns = _header_indexes(sheet, "create")
            signature_col = int(columns["signature"])
            occurrences_col = columns["occurrences"]
            occurrences_col = int(occurrences_col) if occurrences_col is not None else None
            for row_no, cells in sheet.rows:
                signature = _coerce_signature_id(
                    cells[signature_col] if signature_col < len(cells) else "",
                )
                if signature is None or signature not in registered_ids:
                    rejected.append(
                        f"{sheet.name}!{row_no} signature_id outside registered scope "
                        f"({cells[signature_col] if signature_col < len(cells) else ''!r})",
                    )
                    continue
                if occurrences_col is None:
                    continue
                raw_ids = cells[occurrences_col] if occurrences_col < len(cells) else ""
                occurrence_ids, invalid = _parse_occurrence_ids(raw_ids)
                if invalid:
                    rejected.append(f"{sheet.name}!{row_no} invalid occurrence ids {invalid}")
                    continue
                out_of_scope = [
                    occ for occ in occurrence_ids
                    if occ not in candidates_by_sig.get(signature, set())
                ]
                if out_of_scope:
                    rejected.append(
                        f"{sheet.name}!{row_no} occurrence_ids outside signature {signature}: {out_of_scope}",
                    )
        if rejected:
            detail = "; ".join(rejected[:5])
            suffix_text = "" if len(rejected) <= 5 else f" (+{len(rejected) - 5} more)"
            raise JiraDeliveryError(
                409,
                f"uploaded table contains rows outside registered delivery scope: {detail}{suffix_text}",
            )
        return

    relations: list[dict[str, object]] = []
    seen_relations: set[tuple[Optional[int], str, str, str]] = set()
    for item in manifest.get("issues") or []:
        if not isinstance(item, Mapping):
            continue
        relation = (
            _coerce_signature_id(item.get("signature_id")),
            str(item.get("normalized_key") or "").strip(),
            str(item.get("signature_raw") or "").strip(),
            str(item.get("representative_verified_path") or "").strip(),
        )
        if relation in seen_relations:
            continue
        seen_relations.add(relation)
        relations.append({
            "id": relation[0], "key": relation[1], "raw": relation[2], "path": relation[3],
        })
    for item in manifest.get("issue_ids") or []:
        sig_id = _coerce_signature_id(item)
        if sig_id is None or any(rel["id"] == sig_id for rel in relations):
            continue
        relations.append({"id": sig_id, "key": "", "raw": "", "path": ""})
    if not relations:
        raise JiraDeliveryError(
            409,
            "registered delivery scope contains no issues; an attached table cannot be proven in scope",
        )

    rejected = []
    for sheet in sheets:
        columns = _header_indexes(sheet, "dedup")
        signature_col = columns["signature"]
        key_col = columns["key"]
        path_col = columns["path"]
        raw_cols = [int(idx) for idx in columns["raw"] or []]
        for row_no, cells in sheet.rows:
            id_raw = _row_cell(cells, signature_col)
            key_raw = _row_cell(cells, key_col)
            path_val = _row_cell(cells, path_col)
            raw_values = [
                value for value in (_row_cell(cells, idx) for idx in raw_cols) if value
            ]
            if not any((id_raw, key_raw, path_val, raw_values)):
                rejected.append(f"{sheet.name}!{row_no} has no registered identity value")
                continue
            row_id = _coerce_signature_id(id_raw)
            bound = any(
                (not id_raw or row_id == rel["id"])
                and (not key_raw or key_raw == rel["key"])
                and (not path_val or path_val == rel["path"])
                and all(raw in (rel["raw"],) for raw in raw_values)
                for rel in relations
            )
            if not bound:
                rejected.append(
                    f"{sheet.name}!{row_no} identity/path do not bind to one registered issue "
                    f"(id={id_raw or '-'}, key={key_raw or '-'}, path={path_val or '-'})",
                )
    if rejected:
        detail = "; ".join(rejected[:5])
        suffix_text = "" if len(rejected) <= 5 else f" (+{len(rejected) - 5} more)"
        raise JiraDeliveryError(
            409,
            f"uploaded table contains rows outside registered delivery scope: {detail}{suffix_text}",
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
            uploaded_suffix=uploaded_suffix, stage=stage,
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
            uploaded_suffix=uploaded_suffix, stage=stage,
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


def _sanitize_vendor_gap(entry: object) -> Optional[dict[str, Any]]:
    """把厂商 gaps 条目收敛为可查询的 JSON 字段，保留契约中的诊断信息。"""
    if not isinstance(entry, Mapping):
        return None
    kind = str(entry.get("kind") or "").strip()
    if not kind:
        return None
    gap: dict[str, Any] = {"kind": kind, "source": "vendor_attribution"}
    for field in (
        "stage", "row", "workbook_row", "signature_id", "occurrence_ids",
        "occurrence_keys", "reason", "duplicate_occurrence_ids",
        "raw_signature_id", "raw_occurrence_ids",
    ):
        if field not in entry:
            continue
        value = entry[field]
        if value is None or isinstance(value, (str, int, float, bool)):
            gap[field] = value
        elif isinstance(value, (list, tuple)):
            gap[field] = [
                item for item in value if isinstance(item, (str, int, float, bool))
            ]
    return gap


def _dedupe_gaps(gaps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for gap in gaps:
        key = json.dumps(gap, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(gap))
    if len(out) <= 500:
        return out
    return out[:499] + [{
        "kind": "attribution_gaps_truncated",
        "source": "platform",
        "reason": f"{len(out) - 499} additional gaps omitted",
    }]


def _extract_vendor_result_artifacts(
    workspace_dir: Optional[str],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """读取 #27 契约的 links / gaps 与（若有）厂商 result JSON 列表。"""
    if not workspace_dir:
        return [], [], [], []
    ws = Path(workspace_dir).resolve(strict=False)
    if not ws.is_dir():
        return [], [], [], []

    structured_links: list[dict[str, Any]] = []
    artifact_gaps: list[dict[str, Any]] = []
    for attr_name in ("attribution.json", "jira_attribution.json"):
        attr_file = ws / attr_name
        if attr_file.is_file() and not attr_file.is_symlink():
            try:
                data = json.loads(attr_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                artifact_gaps.append({
                    "kind": "vendor_attribution_artifact_unreadable",
                    "source": "vendor_attribution",
                    "reason": f"{attr_name} is not valid JSON",
                })
                continue
            if isinstance(data, Mapping):
                schema = str(data.get("schema") or "").strip()
                contract = str(data.get("contract_version") or "").strip()
                if schema and schema != "stp.jira_attribution/v1":
                    artifact_gaps.append({
                        "kind": "vendor_attribution_contract_mismatch",
                        "source": "vendor_attribution",
                        "reason": f"unsupported artifact schema {schema!r}",
                    })
                if contract and contract != "b8-g8-attribution-v1":
                    artifact_gaps.append({
                        "kind": "vendor_attribution_contract_mismatch",
                        "source": "vendor_attribution",
                        "reason": f"unsupported contract version {contract!r}",
                    })
                for entry in data.get("gaps") or []:
                    gap = _sanitize_vendor_gap(entry)
                    if gap is not None:
                        artifact_gaps.append(gap)
                items = data.get("links")
            else:
                items = data
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

    return structured_links, result_keys, result_rows, artifact_gaps


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
    structured_links, result_keys, result_rows, vendor_gaps = _extract_vendor_result_artifacts(
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
    gaps: list[dict[str, Any]] = list(vendor_gaps)
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

    gaps = _dedupe_gaps(gaps)
    if jira_run.stage != "create":
        jira_run.attribution_status = ATTRIBUTION_NOT_APPLICABLE
    elif verified_links and not gaps:
        jira_run.attribution_status = ATTRIBUTION_VERIFIED
    elif verified_links:
        jira_run.attribution_status = ATTRIBUTION_PARTIAL
    elif gaps:
        jira_run.attribution_status = ATTRIBUTION_UNATTRIBUTED
    else:
        jira_run.attribution_status = ATTRIBUTION_NOT_APPLICABLE
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
