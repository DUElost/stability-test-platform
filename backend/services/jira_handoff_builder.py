"""B11-U1（#4199 / #4174）阶段 1：A 段只读提单表单生成引擎与冻结产物存储。

职责边界：
- 仅运行控制面轻量 ``start_log_scan.py -dedup_org``（V2.1.1，消费 ``*_members.json``），
  严禁调用 ``-m`` / ``-m 0`` / ``aee_extract``。
- 仅落盘中心存储受控目录 ``_meta/handoff/<handoff_id>/``（零 15.4 外部写入、零 DB 迁移），
  并经既有 ``record_audit`` 留痕（ADR-0015）。
- 所有 A 段产物恒标 ``status="PENDING_EXPORT"``，预览 ``.xls`` 的 ``Path`` 列写显式
  ``PENDING_EXPORT://...`` 占位，绝不伪造未落盘的厂商共享路径。
- 不导入 FastAPI（C2 分层约束）；路由层负责将 ``JiraHandoffError`` 翻译为 HTTP 响应。
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterator, Mapping, Optional, Sequence
import uuid

from sqlalchemy.orm import Session
import xlrd
import xlwt

from backend.core.storage_root import resolve_shared_storage_root
from backend.services.audit_writer import record_audit
from backend.services.dedup_scan import resolve_scan_tool
from backend.services.jira_handoff_identity import (
    FORM_COLUMNS,
    HANDOFF_STATUS_PENDING_EXPORT,
    ORG_COLUMNS,
    SUPPORTED_HANDOFF_PLATFORMS,
    DedupedEvent,
    IdentityCollectionResult,
    classify_issue_baseline,
    collect_handoff_identity,
    safe_xls_cell,
)

logger = logging.getLogger(__name__)

HANDOFF_MANIFEST_SCHEMA = "stp.jira_handoff_manifest"
HANDOFF_MANIFEST_SCHEMA_VERSION = "1.0"
HANDOFF_DOWNLOAD_KINDS = frozenset({"manifest", "preview_xls", "review_workbook"})

_HANDOFF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$")
_SAFE_BUILD_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEDUP_ORG_TIMEOUT_SECONDS = 300
_PENDING_EXPORT_PATH_PREFIX = "PENDING_EXPORT://"


@dataclass(frozen=True)
class JiraHandoffError(Exception):
    """A 段服务层结构化异常；由路由层映射为 HTTPException。"""

    status_code: int
    code: str
    detail: str
    payload: Optional[dict[str, Any]] = None

    def __str__(self) -> str:
        return self.detail


def validate_platform(platform: str) -> str:
    """一期仅支持 MTK（#4199 §3 B11-U1 #8），非 mtk 返回 400 fail-closed。"""
    normalized = (platform or "").strip().lower()
    if normalized not in SUPPORTED_HANDOFF_PLATFORMS:
        raise JiraHandoffError(
            status_code=400,
            code="handoff_unsupported_platform",
            detail=f"platform {platform!r} is not supported in B11 stage 1 (only 'mtk' is allowed)",
        )
    return normalized


def validate_handoff_id(handoff_id: str) -> str:
    cleaned = (handoff_id or "").strip()
    if not _HANDOFF_ID_RE.match(cleaned):
        raise JiraHandoffError(
            status_code=400,
            code="handoff_invalid_id",
            detail=f"invalid handoff_id: {handoff_id!r}",
        )
    return cleaned


def _build_slug(build: str) -> str:
    slug = _SAFE_BUILD_SLUG_RE.sub("_", (build or "").strip()).strip("._-")
    if not slug or slug in (".", ".."):
        return "unknown_build"
    return slug[:96]


def _resolve_handoff_base_dir(storage_root: Optional[str] = None) -> Path:
    root = (storage_root or "").strip() or resolve_shared_storage_root()
    if not root:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_storage_unconfigured",
            detail="shared storage root (STP_AEE_NFS_ROOT) is not configured",
        )
    return Path(root).resolve(strict=False) / "_meta" / "handoff"


def _atomic_write_bytes(path: Path, data: bytes) -> str:
    """原子落盘（tmp + fsync + os.replace）并返回 sha256 十六进制摘要。"""
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def compute_manifest_sha256(manifest_without_digest: Mapping[str, Any]) -> str:
    """对去除 ``manifest_sha256`` 字段的规范化 manifest 载荷求 sha256（供 B11-U3 验签）。"""
    clean = {k: v for k, v in manifest_without_digest.items() if k != "manifest_sha256"}
    return hashlib.sha256(_canonical_json_bytes(clean)).hexdigest()


@contextmanager
def _exclusive_dedup_org_lock(tool_dir: Path) -> Iterator[None]:
    """跨请求文件锁：防止并发 ``-dedup_org`` 调用在共享工具目录互相干扰。"""
    lock_dir = tool_dir / "tmp"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".stp_handoff_dedup_org.lock"
    with lock_path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _write_build_org_input_xls(
    dest: Path,
    *,
    build: str,
    events: Sequence[DedupedEvent],
    folder_hint: str,
) -> None:
    """为单个 build 构造规范 15 列 ``<build>_org.xls``（供 ``-dedup_org`` 消费）。"""
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("aeeexp", cell_overwrite_ok=True)
    for col_idx, name in enumerate(ORG_COLUMNS):
        ws.write(0, col_idx, name)
    for row_idx, ev in enumerate(events, start=1):
        # 路径使用唯一占位形态（不含真实内网 IP 或未落盘厂商挂载前缀）
        synthetic_path = (
            f"{_PENDING_EXPORT_PATH_PREFIX}{folder_hint}/{ev.serial}/aee_exp/{ev.entry}/"
        )
        f = ev.fields
        values = [
            row_idx - 1,
            synthetic_path,
            build,
            f.get("ExpTime", ""),
            f.get("ExpClass", ""),
            f.get("ExpType", ""),
            f.get("CurProcess", ""),
            f.get("Package", ""),
            f.get("Detail", ""),
            f.get("CausedBy", ""),
            f.get("extraTag", ""),
            1,
            f.get("Activity", ""),
            ev.serial,
            f.get("Rom_Ram", ""),
        ]
        for col_idx, val in enumerate(values):
            ws.write(row_idx, col_idx, safe_xls_cell(val))
    wb.save(str(dest))


def _read_dedup_final_xls(path: Path) -> list[dict[str, str]]:
    book = xlrd.open_workbook(str(path), on_demand=True)
    try:
        sheet = (
            book.sheet_by_name("aeeexp")
            if "aeeexp" in book.sheet_names()
            else book.sheet_by_index(0)
        )
        if sheet.nrows <= 1:
            return []
        headers = [str(v).strip() for v in sheet.row_values(0)]
        col_map = {h.lower(): i for i, h in enumerate(headers)}

        def _val(vals: Sequence[Any], key: str) -> str:
            idx = col_map.get(key.lower())
            if idx is None or idx >= len(vals):
                return ""
            raw = vals[idx]
            if raw is None:
                return ""
            if isinstance(raw, float) and raw.is_integer():
                return str(int(raw))
            return str(raw).strip()

        rows: list[dict[str, str]] = []
        for i in range(1, sheet.nrows):
            vals = sheet.row_values(i)
            rows.append(
                {
                    "ExpTime": _val(vals, "ExpTime"),
                    "ExpClass": _val(vals, "ExpClass"),
                    "ExpType": _val(vals, "ExpType"),
                    "CurProcess": _val(vals, "CurProcess"),
                    "Package": _val(vals, "Package"),
                    "Detail": _val(vals, "Detail"),
                    "CausedBy": _val(vals, "CausedBy"),
                    "extraTag": _val(vals, "extraTag"),
                    "Activity": _val(vals, "Activity"),
                    "Rom_Ram": _val(vals, "Rom_Ram"),
                }
            )
        return rows
    finally:
        book.release_resources()


def _invoke_dedup_org_tool(
    tool: Mapping[str, str],
    org_xls: Path,
    *,
    side: str = "shanghai",
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """只读调用 V2.1.1 ``start_log_scan.py -dedup_org`` 并强制校验 ``*_members.json``。"""
    python_bin = str(tool.get("python") or "").strip()
    script_path = Path(str(tool.get("script") or "").strip())
    if not python_bin or not script_path.is_file():
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_unavailable",
            detail="backend dedup scan tool is not configured or script file missing",
        )
    argv = [
        python_bin,
        str(script_path),
        "-dedup_org",
        str(org_xls),
        "-side",
        side,
    ]
    with _exclusive_dedup_org_lock(script_path.parent):
        try:
            proc = subprocess.run(
                argv,
                cwd=str(script_path.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_DEDUP_ORG_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise JiraHandoffError(
                status_code=504,
                code="handoff_dedup_org_timeout",
                detail=f"-dedup_org timed out after {_DEDUP_ORG_TIMEOUT_SECONDS}s",
            ) from exc
        except OSError as exc:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_dedup_org_spawn_failed",
                detail=f"failed to execute -dedup_org: {exc}",
            ) from exc

    if proc.returncode != 0:
        raise JiraHandoffError(
            status_code=502,
            code="handoff_dedup_org_failed",
            detail=f"-dedup_org exited with code {proc.returncode}: {(proc.stderr or '')[:300]}",
        )

    members_files = sorted(org_xls.parent.glob("*_members.json"))
    if not members_files:
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_missing",
            detail="-dedup_org did not produce *_members.json (requires start-log-scan >= 2.1.0)",
        )
    members_path = members_files[-1]
    try:
        members_data = json.loads(members_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail=f"failed to parse *_members.json: {exc}",
        ) from exc
    if not isinstance(members_data, dict) or not isinstance(members_data.get("rows"), list):
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail="*_members.json lacks valid rows list",
        )

    dedup_xls_files = sorted(org_xls.parent.glob("*_dedup_org_*.xls"))
    final_rows = _read_dedup_final_xls(dedup_xls_files[-1]) if dedup_xls_files else []
    return final_rows, members_data


def _validate_and_aggregate_groups(
    *,
    build: str,
    events: Sequence[DedupedEvent],
    final_rows: Sequence[Mapping[str, str]],
    members_data: Mapping[str, Any],
    folder_hint: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """依据 ``*_members.json`` 的分组与过滤结果，按唯一发生重算 ``Count`` / ``DeviceCount``。"""
    rows = members_data.get("rows") or []
    groups_raw = members_data.get("groups") or []
    if len(rows) != len(events):
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_row_count_mismatch",
            detail=f"*_members.json row count ({len(rows)}) != input unique events ({len(events)})",
        )

    by_org_row: dict[int, Mapping[str, Any]] = {}
    filtered_rows: list[dict[str, Any]] = []
    filtered_by_reason: Counter[str] = Counter()
    for m in rows:
        if not isinstance(m, dict) or "org_row" not in m:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail="*_members.json row missing org_row",
            )
        org_row = int(m["org_row"])
        by_org_row[org_row] = m
        if m.get("branch") == "filtered":
            ev = events[org_row - 1]
            reason = str(m.get("filter_reason") or "filtered")
            filtered_by_reason[reason] += 1
            filtered_rows.append(
                {
                    "dedup_key": ev.dedup_key,
                    "serial": ev.serial,
                    "entry": ev.entry,
                    "reason": reason,
                    "package": ev.fields.get("Package", ""),
                    "exp_type": ev.fields.get("ExpType", ""),
                }
            )
    if set(by_org_row) != set(range(1, len(events) + 1)):
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail="*_members.json org_row indices are not contiguous 1..N",
        )

    covered: set[int] = set()
    issues: list[dict[str, Any]] = []
    for idx, g in enumerate(groups_raw, start=1):
        if not isinstance(g, dict):
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail="invalid group entry in *_members.json",
            )
        member_nums = [int(n) for n in (g.get("member_rows") or [])]
        rep_row = int(g.get("representative_row") or (member_nums[0] if member_nums else 0))
        if not member_nums or rep_row not in member_nums:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} has invalid member_rows/representative_row",
            )
        if covered.intersection(member_nums):
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} overlaps previous group rows",
            )
        covered.update(member_nums)

        member_events = [events[n - 1] for n in member_nums]
        baseline_class = classify_issue_baseline([ev.baseline_class for ev in member_events])
        # 代表选择：混合问题优先取「本次」最早事件，否则取最早事件（对齐 SKILL.md 第 13 步）
        if baseline_class == "mixed":
            current_members = [ev for ev in member_events if ev.baseline_class == "current"]
            rep_event = current_members[0] if current_members else events[rep_row - 1]
        else:
            rep_event = events[rep_row - 1]

        final_row_idx = int(g.get("final_row") or idx) - 1
        final_fields = (
            final_rows[final_row_idx]
            if 0 <= final_row_idx < len(final_rows)
            else rep_event.fields
        )
        device_ids = sorted({ev.serial for ev in member_events if ev.serial})
        source_runs = sorted({rid for ev in member_events for rid in ev.source_runs})
        preview_path = (
            f"{_PENDING_EXPORT_PATH_PREFIX}{folder_hint}/{rep_event.serial}"
            f"/aee_exp/{rep_event.entry}/<dec_pending>/__exp_main.txt"
        )
        issues.append(
            {
                "issue_index": idx,
                "build": build,
                "count": len(member_events),
                "device_count": len(device_ids),
                "device_ids": device_ids,
                "source_runs": source_runs,
                "baseline_class": baseline_class,
                "clock_fallback_count": sum(1 for ev in member_events if ev.clock_fallback),
                "preview_path": preview_path,
                "representative": {
                    "dedup_key": rep_event.dedup_key,
                    "identity_source": rep_event.identity_source,
                    "occurrence_key": rep_event.occurrence_key,
                    "dle_id": rep_event.primary_dle_id,
                    "serial": rep_event.serial,
                    "entry": rep_event.entry,
                    "evidence_location": rep_event.evidence_location,
                    "evidence_tier": rep_event.evidence_tier,
                },
                "member_dedup_keys": [ev.dedup_key for ev in member_events],
                "form_fields": {
                    "Version": build,
                    "ExpTime": final_fields.get("ExpTime") or rep_event.fields.get("ExpTime", ""),
                    "ExpClass": final_fields.get("ExpClass") or rep_event.fields.get("ExpClass", ""),
                    "ExpType": final_fields.get("ExpType") or rep_event.fields.get("ExpType", ""),
                    "CurProcess": final_fields.get("CurProcess") or rep_event.fields.get("CurProcess", ""),
                    "Package": final_fields.get("Package") or rep_event.fields.get("Package", ""),
                    "Detail": final_fields.get("Detail") or rep_event.fields.get("Detail", ""),
                    "CausedBy": final_fields.get("CausedBy") or rep_event.fields.get("CausedBy", ""),
                    "extraTag": final_fields.get("extraTag") or "",
                    "Activity": final_fields.get("Activity") or rep_event.fields.get("Activity", ""),
                    "Rom_Ram": final_fields.get("Rom_Ram") or rep_event.fields.get("Rom_Ram", ""),
                },
            }
        )

    expected_grouped = {n for n, m in by_org_row.items() if m.get("branch") != "filtered"}
    if covered != expected_grouped:
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_coverage_mismatch",
            detail="*_members.json groups do not partition all non-filtered rows",
        )
    if sum(item["count"] for item in issues) + len(filtered_rows) != len(events):
        raise JiraHandoffError(
            status_code=502,
            code="handoff_count_invariant_failed",
            detail="sum(issue.count) + filtered != unique_events",
        )
    return issues, filtered_rows, dict(sorted(filtered_by_reason.items()))


def _render_preview_form_xls(
    dest: Path,
    *,
    build: str,
    issues: Sequence[Mapping[str, Any]],
) -> str:
    """渲染 A 段 15 列预览 ``.xls``（严格 15 列表头、数值保留整型、文本走 B3 公式中和）。"""
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("aeeexp", cell_overwrite_ok=True)
    widths = [1000, 10000, 6000, 7000, 2000, 4000, 7000, 7000, 30000, 30000, 7000, 5000, 7000, 10000, 8000]
    for col_idx, width in enumerate(widths):
        ws.col(col_idx).width = width
    st_hdr = xlwt.easyxf("font: height 200, name Times New Roman, color-index blue, bold on")
    st_body = xlwt.easyxf("font: name calibri")

    for col_idx, col_name in enumerate(FORM_COLUMNS):
        ws.write(0, col_idx, col_name, st_hdr)

    for row_idx, item in enumerate(issues, start=1):
        ff = item["form_fields"]
        values: list[Any] = [
            row_idx - 1,
            item["preview_path"],
            build,
            ff.get("ExpTime", ""),
            ff.get("ExpClass", ""),
            ff.get("ExpType", ""),
            ff.get("CurProcess", ""),
            ff.get("Package", ""),
            ff.get("Detail", ""),
            ff.get("CausedBy", ""),
            ff.get("extraTag", ""),
            int(item["count"]),
            ff.get("Activity", ""),
            int(item["device_count"]),
            ff.get("Rom_Ram", ""),
        ]
        for col_idx, val in enumerate(values):
            ws.write(row_idx, col_idx, safe_xls_cell(val), st_body)

    with tempfile.NamedTemporaryFile(dir=str(dest.parent), suffix=".xls", delete=False) as fh:
        tmp_path = Path(fh.name)
    try:
        wb.save(str(tmp_path))
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return _atomic_write_bytes(dest, data)


def _render_review_workbook_xls(
    dest: Path,
    *,
    handoff_id: str,
    build: str,
    collection: IdentityCollectionResult,
    issues: Sequence[Mapping[str, Any]],
    events: Sequence[DedupedEvent],
    filtered_rows: Sequence[Mapping[str, Any]],
) -> str:
    """渲染 5 页复核工作簿 ``review_workbook.xls``（全部文本单元走 B3 公式中和）。"""
    wb = xlwt.Workbook(encoding="utf-8")

    # Sheet 1: 概览
    ws1 = wb.add_sheet("概览", cell_overwrite_ok=True)
    baseline_counts = Counter(item["baseline_class"] for item in issues)
    overview_rows = [
        ("字段", "值"),
        ("交付包 ID", handoff_id),
        ("交付状态", f"{HANDOFF_STATUS_PENDING_EXPORT}（待阶段 B 导出与厂商 Path 校验，不可直接提单）"),
        ("Build", build),
        ("快照截止时间(UTC)", collection.snapshot_cutoff.isoformat()),
        ("首轮开始时间(UTC)", collection.first_run_started_at.isoformat() if collection.first_run_started_at else ""),
        ("来源 PlanRun", ",".join(str(r.plan_run_id) for r in collection.runs)),
        ("覆盖率完整", "是" if collection.coverage_complete else "否"),
        ("唯一事件数", len(events)),
        ("去重后问题数", len(issues)),
        ("问题-仅本次", baseline_counts.get("current_only", 0)),
        ("问题-混合", baseline_counts.get("mixed", 0)),
        ("问题-仅基线（仅标注不自动剔除）", baseline_counts.get("baseline_only", 0)),
        ("工具过滤事件数", len(filtered_rows)),
        ("未绑定/占位行数（全批）", len(collection.unbound_rows)),
    ]
    for r_idx, (k, v) in enumerate(overview_rows):
        ws1.write(r_idx, 0, safe_xls_cell(k))
        ws1.write(r_idx, 1, safe_xls_cell(v))

    # Sheet 2: 问题汇总
    ws2 = wb.add_sheet("问题汇总", cell_overwrite_ok=True)
    s2_headers = [
        "序号", "本次/基线", "Count", "DeviceCount", "ExpClass", "ExpType",
        "CurProcess", "Package", "CausedBy", "来源Runs", "预览Path(待导出)",
    ]
    for c_idx, h in enumerate(s2_headers):
        ws2.write(0, c_idx, h)
    for r_idx, item in enumerate(issues, start=1):
        ff = item["form_fields"]
        vals = [
            int(item["issue_index"]),
            item["baseline_class"],
            int(item["count"]),
            int(item["device_count"]),
            ff.get("ExpClass", ""),
            ff.get("ExpType", ""),
            ff.get("CurProcess", ""),
            ff.get("Package", ""),
            ff.get("CausedBy", ""),
            ",".join(str(x) for x in item["source_runs"]),
            item["preview_path"],
        ]
        for c_idx, v in enumerate(vals):
            ws2.write(r_idx, c_idx, safe_xls_cell(v))

    # Sheet 3: 代表日志清单
    ws3 = wb.add_sheet("代表日志清单", cell_overwrite_ok=True)
    s3_headers = ["问题序号", "代表串号", "事件目录名", "身份来源", "occurrence_key", "DLE_ID", "当前证据层", "当前证据路径"]
    for c_idx, h in enumerate(s3_headers):
        ws3.write(0, c_idx, h)
    for r_idx, item in enumerate(issues, start=1):
        rep = item["representative"]
        vals = [
            int(item["issue_index"]),
            rep["serial"],
            rep["entry"],
            rep["identity_source"],
            rep.get("occurrence_key") or "",
            rep.get("dle_id") or "",
            rep["evidence_tier"],
            rep["evidence_location"],
        ]
        for c_idx, v in enumerate(vals):
            ws3.write(r_idx, c_idx, safe_xls_cell(v))

    # Sheet 4: 全部唯一事件
    ws4 = wb.add_sheet("全部唯一事件", cell_overwrite_ok=True)
    s4_headers = [
        "去重键", "身份来源", "串号", "事件目录名", "ExpTime", "本次/基线",
        "坏钟回退", "来源Runs", "ExpClass", "Package",
    ]
    for c_idx, h in enumerate(s4_headers):
        ws4.write(0, c_idx, h)
    for r_idx, ev in enumerate(events, start=1):
        vals = [
            ev.dedup_key,
            ev.identity_source,
            ev.serial,
            ev.entry,
            ev.fields.get("ExpTime", ""),
            ev.baseline_class,
            "是" if ev.clock_fallback else "否",
            ",".join(str(x) for x in ev.source_runs),
            ev.fields.get("ExpClass", ""),
            ev.fields.get("Package", ""),
        ]
        for c_idx, v in enumerate(vals):
            ws4.write(r_idx, c_idx, safe_xls_cell(v))

    # Sheet 5: 未绑定与口径说明
    ws5 = wb.add_sheet("未绑定与口径说明", cell_overwrite_ok=True)
    s5_headers = ["类别", "PlanRun", "Host", "报表文件", "行号", "串号", "原因", "原始路径"]
    for c_idx, h in enumerate(s5_headers):
        ws5.write(0, c_idx, h)
    row_cursor = 1
    for ub in collection.unbound_rows:
        vals = [
            "unbound",
            int(ub.plan_run_id),
            ub.host_id,
            ub.report_file,
            int(ub.row_no),
            ub.serial,
            ub.reason,
            ub.raw_path,
        ]
        for c_idx, v in enumerate(vals):
            ws5.write(row_cursor, c_idx, safe_xls_cell(v))
        row_cursor += 1
    for fr in filtered_rows:
        vals = [
            "filtered",
            0,
            "",
            "",
            0,
            fr.get("serial", ""),
            fr.get("reason", ""),
            fr.get("entry", ""),
        ]
        for c_idx, v in enumerate(vals):
            ws5.write(row_cursor, c_idx, safe_xls_cell(v))
        row_cursor += 1

    with tempfile.NamedTemporaryFile(dir=str(dest.parent), suffix=".xls", delete=False) as fh:
        tmp_path = Path(fh.name)
    try:
        wb.save(str(tmp_path))
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return _atomic_write_bytes(dest, data)


def run_handoff_preflight(
    db: Session,
    *,
    plan_run_ids: Sequence[int],
    platform: str = "mtk",
    snapshot_cutoff: Optional[datetime] = None,
) -> dict[str, Any]:
    """A 段只读双门禁预检：零写盘、零外部子进程调用。"""
    norm_platform = validate_platform(platform)
    if not plan_run_ids:
        raise JiraHandoffError(400, "handoff_empty_runs", "plan_run_ids must not be empty")
    try:
        col = collect_handoff_identity(
            db,
            plan_run_ids=plan_run_ids,
            platform=norm_platform,
            snapshot_cutoff=snapshot_cutoff,
        )
    except LookupError as exc:
        raise JiraHandoffError(404, "handoff_plan_run_not_found", str(exc)) from exc

    return {
        "platform": norm_platform,
        "snapshot_cutoff": col.snapshot_cutoff.isoformat(),
        "first_run_started_at": (
            col.first_run_started_at.isoformat() if col.first_run_started_at else None
        ),
        "runs": [
            {
                "plan_run_id": r.plan_run_id,
                "plan_name": r.plan_name,
                "status": r.status,
                "partition_mode": r.partition_mode,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "project_key": r.project_key,
                "jira_project_key": r.jira_project_key,
                "hosts_expected": list(r.hosts_expected),
                "hosts_with_reports": list(r.hosts_with_reports),
                "missing_hosts": list(r.missing_hosts),
                "mapping_status": r.mapping_status,
            }
            for r in col.runs
        ],
        "identity_gate": {
            "total_org_rows": col.total_org_rows,
            "unique_events": len(col.events),
            "keyed_rows": col.keyed_rows,
            "compat_fallback_rows": col.compat_fallback_rows,
            "unbound_rows": len(col.unbound_rows),
            "after_cutoff_skipped_rows": col.after_cutoff_skipped_rows,
            "unbound_by_reason": dict(col.unbound_by_reason),
        },
        "builds": sorted(col.builds_meta.values(), key=lambda x: x["build"]),
        "gates": {
            "coverage_complete": col.coverage_complete,
            "identity_degraded": col.identity_degraded,
            "blocking_issues": list(col.blocking_issues),
            "warnings": list(col.warnings),
        },
    }


def create_handoff_bundle(
    db: Session,
    *,
    plan_run_ids: Sequence[int],
    platform: str = "mtk",
    snapshot_cutoff: Optional[datetime] = None,
    builds: Optional[Sequence[str]] = None,
    allow_incomplete_coverage: bool = False,
    target_folder_hint: Optional[str] = None,
    storage_root: Optional[str] = None,
    scan_tool: Optional[Mapping[str, str]] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    request: Optional[Any] = None,
) -> dict[str, Any]:
    """执行 A 段只读提单表单生成，落盘不可变 ``_meta/handoff/<handoff_id>/manifest.json``。"""
    norm_platform = validate_platform(platform)
    if not plan_run_ids:
        raise JiraHandoffError(400, "handoff_empty_runs", "plan_run_ids must not be empty")

    try:
        col = collect_handoff_identity(
            db,
            plan_run_ids=plan_run_ids,
            platform=norm_platform,
            snapshot_cutoff=snapshot_cutoff,
        )
    except LookupError as exc:
        raise JiraHandoffError(404, "handoff_plan_run_not_found", str(exc)) from exc

    if not col.coverage_complete and not allow_incomplete_coverage:
        raise JiraHandoffError(
            status_code=409,
            code="handoff_coverage_incomplete",
            detail="scan coverage gate failed; resolve missing host reports or explicitly allow incomplete coverage",
            payload={
                "blocking_issues": list(col.blocking_issues),
                "missing_hosts_by_run": {
                    str(r.plan_run_id): list(r.missing_hosts)
                    for r in col.runs
                    if r.missing_hosts
                },
            },
        )

    wanted_builds = {b.strip() for b in (builds or []) if b and b.strip()}
    events_by_build: dict[str, list[DedupedEvent]] = {}
    for ev in col.events:
        if wanted_builds and ev.build not in wanted_builds:
            continue
        events_by_build.setdefault(ev.build, []).append(ev)

    if not events_by_build:
        raise JiraHandoffError(
            status_code=422,
            code="handoff_no_input",
            detail="no bound unique events available for the requested plan_run_ids/builds",
        )

    tool = dict(scan_tool) if scan_tool is not None else (resolve_scan_tool() or {})
    if not tool.get("python") or not tool.get("script"):
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_unavailable",
            detail="STP_BACKEND_DEDUP_SCAN_PYTHON / STP_BACKEND_DEDUP_SCAN_SCRIPT not configured",
        )

    base_dir = _resolve_handoff_base_dir(storage_root)
    now_utc = datetime.now(timezone.utc)
    ts_compact = now_utc.strftime("%Y%m%d_%H%M%S")
    handoff_id = f"hf_{ts_compact}_{uuid.uuid4().hex[:8]}"
    bundle_dir = base_dir / handoff_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    side_env = os.getenv("STP_DEDUP_SCAN_TAG", "shanghai")
    side = "factory" if "factory" in side_env.lower() else "shanghai"
    clean_folder_hint = _build_slug(target_folder_hint or "") if target_folder_hint else ""

    build_entries: list[dict[str, Any]] = []
    manifest_written = False
    try:
        for build_name in sorted(events_by_build):
            build_events = events_by_build[build_name]
            slug = _build_slug(build_name)
            folder_label = clean_folder_hint or slug
            with tempfile.TemporaryDirectory(prefix=f"stp-handoff-{slug}-") as tmpdir:
                tmp_root = Path(tmpdir)
                org_input = tmp_root / f"{slug}_org.xls"
                _write_build_org_input_xls(
                    org_input,
                    build=build_name,
                    events=build_events,
                    folder_hint=folder_label,
                )
                final_rows, members_data = _invoke_dedup_org_tool(tool, org_input, side=side)

            issues, filtered_rows, filtered_by_reason = _validate_and_aggregate_groups(
                build=build_name,
                events=build_events,
                final_rows=final_rows,
                members_data=members_data,
                folder_hint=folder_label,
            )

            build_dir = bundle_dir / "builds" / slug
            build_dir.mkdir(parents=True, exist_ok=True)
            preview_filename = f"preview_Result_None_None_MonkeyAEE_SH_{ts_compact}.xls"
            review_filename = "review_workbook.xls"
            preview_rel = f"builds/{slug}/{preview_filename}"
            review_rel = f"builds/{slug}/{review_filename}"

            preview_sha = _render_preview_form_xls(
                bundle_dir / preview_rel,
                build=build_name,
                issues=issues,
            )
            review_sha = _render_review_workbook_xls(
                bundle_dir / review_rel,
                handoff_id=handoff_id,
                build=build_name,
                collection=col,
                issues=issues,
                events=build_events,
                filtered_rows=filtered_rows,
            )

            baseline_dist = Counter(item["baseline_class"] for item in issues)
            build_meta = col.builds_meta.get(build_name, {})
            build_entries.append(
                {
                    "build": build_name,
                    "build_slug": slug,
                    "product": build_meta.get("product", ""),
                    "device_count": len({ev.serial for ev in build_events}),
                    "unique_events": len(build_events),
                    "issue_count": len(issues),
                    "grouped_events": sum(item["count"] for item in issues),
                    "filtered_events": len(filtered_rows),
                    "filtered_by_reason": filtered_by_reason,
                    "baseline_distribution": {
                        "current_only": baseline_dist.get("current_only", 0),
                        "mixed": baseline_dist.get("mixed", 0),
                        "baseline_only": baseline_dist.get("baseline_only", 0),
                    },
                    "artifacts": {
                        "preview_xls": {
                            "rel_path": preview_rel,
                            "filename": preview_filename,
                            "sha256": preview_sha,
                            "size_bytes": (bundle_dir / preview_rel).stat().st_size,
                        },
                        "review_workbook": {
                            "rel_path": review_rel,
                            "filename": review_filename,
                            "sha256": review_sha,
                            "size_bytes": (bundle_dir / review_rel).stat().st_size,
                        },
                    },
                    "issues": issues,
                    "filtered_rows": filtered_rows,
                    "events": [ev.to_manifest_dict() for ev in build_events],
                }
            )

        manifest_body: dict[str, Any] = {
            "schema": HANDOFF_MANIFEST_SCHEMA,
            "schema_version": HANDOFF_MANIFEST_SCHEMA_VERSION,
            "handoff_id": handoff_id,
            "status": HANDOFF_STATUS_PENDING_EXPORT,
            "platform": norm_platform,
            "created_at": now_utc.isoformat(),
            "created_by": username or (str(user_id) if user_id is not None else "unknown"),
            "plan_run_ids": [r.plan_run_id for r in col.runs],
            "snapshot_cutoff": col.snapshot_cutoff.isoformat(),
            "first_run_started_at": (
                col.first_run_started_at.isoformat() if col.first_run_started_at else None
            ),
            "allow_incomplete_coverage": bool(allow_incomplete_coverage),
            "target_folder_hint": clean_folder_hint or None,
            "gates": {
                "coverage_complete": col.coverage_complete,
                "identity_degraded": col.identity_degraded,
                "blocking_issues": list(col.blocking_issues),
                "warnings": list(col.warnings),
            },
            "identity_gate": {
                "total_org_rows": col.total_org_rows,
                "unique_events": len(col.events),
                "keyed_rows": col.keyed_rows,
                "compat_fallback_rows": col.compat_fallback_rows,
                "unbound_rows": len(col.unbound_rows),
                "after_cutoff_skipped_rows": col.after_cutoff_skipped_rows,
                "unbound_by_reason": dict(col.unbound_by_reason),
            },
            "runs": [
                {
                    "plan_run_id": r.plan_run_id,
                    "plan_name": r.plan_name,
                    "status": r.status,
                    "partition_mode": r.partition_mode,
                    "project_key": r.project_key,
                    "jira_project_key": r.jira_project_key,
                    "hosts_expected": list(r.hosts_expected),
                    "hosts_with_reports": list(r.hosts_with_reports),
                    "missing_hosts": list(r.missing_hosts),
                    "mapping_status": r.mapping_status,
                }
                for r in col.runs
            ],
            "unbound_rows": [u.to_dict() for u in col.unbound_rows],
            "builds": build_entries,
        }
        manifest_sha256 = compute_manifest_sha256(manifest_body)
        manifest_body["manifest_sha256"] = manifest_sha256

        manifest_path = bundle_dir / "manifest.json"
        _atomic_write_bytes(manifest_path, _canonical_json_bytes(manifest_body))
        manifest_written = True
    finally:
        if not manifest_written and bundle_dir.exists():
            shutil.rmtree(bundle_dir, ignore_errors=True)

    record_audit(
        db,
        action="jira_handoff_bundle_created",
        resource_type="jira_handoff",
        resource_id=handoff_id,
        details={
            "platform": norm_platform,
            "status": HANDOFF_STATUS_PENDING_EXPORT,
            "plan_run_ids": manifest_body["plan_run_ids"],
            "builds": [b["build"] for b in build_entries],
            "issue_count_total": sum(b["issue_count"] for b in build_entries),
            "coverage_complete": col.coverage_complete,
            "allow_incomplete_coverage": bool(allow_incomplete_coverage),
            "manifest_sha256": manifest_sha256,
        },
        user_id=user_id,
        username=username,
        request=request,
    )
    db.commit()

    return manifest_body


def _find_bundle_dir_untainted(base_dir: Path, clean_id: str) -> Path:
    """从受控根目录直接枚举定位 bundle 目录，切断用户输入直接拼接路径的污点链。"""
    if not base_dir.is_dir():
        raise JiraHandoffError(404, "handoff_bundle_not_found", f"handoff bundle not found: {clean_id}")
    resolved_base = base_dir.resolve(strict=True)
    for child in resolved_base.iterdir():
        if child.name != clean_id:
            continue
        if child.is_symlink() or not child.is_dir():
            raise JiraHandoffError(400, "handoff_symlink_rejected", "handoff bundle dir must not be a symlink")
        return child
    raise JiraHandoffError(404, "handoff_bundle_not_found", f"handoff bundle not found: {clean_id}")


def _load_manifest_strict(handoff_id: str, storage_root: Optional[str] = None) -> tuple[Path, dict[str, Any]]:
    clean_id = validate_handoff_id(handoff_id)
    base_dir = _resolve_handoff_base_dir(storage_root)
    bundle_dir = _find_bundle_dir_untainted(base_dir, clean_id)
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise JiraHandoffError(404, "handoff_bundle_not_found", f"handoff bundle not found: {clean_id}")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise JiraHandoffError(400, "handoff_symlink_rejected", "handoff manifest path must not follow symlinks")
    resolved = manifest_path.resolve(strict=True)
    if resolved != manifest_path:
        raise JiraHandoffError(400, "handoff_symlink_rejected", "handoff manifest path must not follow symlinks")
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JiraHandoffError(500, "handoff_manifest_corrupted", f"failed to read manifest: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != HANDOFF_MANIFEST_SCHEMA:
        raise JiraHandoffError(500, "handoff_manifest_corrupted", "invalid handoff manifest schema")
    return bundle_dir, data


def get_handoff_bundle(
    handoff_id: str,
    *,
    storage_root: Optional[str] = None,
) -> dict[str, Any]:
    _, data = _load_manifest_strict(handoff_id, storage_root=storage_root)
    return data


def list_handoff_bundles(
    *,
    limit: int = 20,
    storage_root: Optional[str] = None,
) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    base_dir = _resolve_handoff_base_dir(storage_root)
    if not base_dir.is_dir():
        return []
    summaries: list[dict[str, Any]] = []
    for child in base_dir.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        if not _HANDOFF_ID_RE.match(child.name):
            continue
        manifest_path = child / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("schema") != HANDOFF_MANIFEST_SCHEMA:
            continue
        builds = data.get("builds") or []
        summaries.append(
            {
                "handoff_id": data.get("handoff_id", child.name),
                "status": data.get("status", HANDOFF_STATUS_PENDING_EXPORT),
                "platform": data.get("platform", "mtk"),
                "created_at": data.get("created_at", ""),
                "created_by": data.get("created_by", ""),
                "plan_run_ids": list(data.get("plan_run_ids") or []),
                "build_count": len(builds),
                "issue_count_total": sum(int(b.get("issue_count", 0)) for b in builds if isinstance(b, dict)),
                "coverage_complete": bool((data.get("gates") or {}).get("coverage_complete", False)),
                "manifest_sha256": data.get("manifest_sha256", ""),
            }
        )
    summaries.sort(key=lambda item: item["created_at"], reverse=True)
    return summaries[:bounded]


def resolve_handoff_download_file(
    handoff_id: str,
    artifact_kind: str,
    *,
    build: Optional[str] = None,
    storage_root: Optional[str] = None,
) -> tuple[Path, str, str]:
    """按白名单定位 A 段可下载产物，拒绝越根与符号链接（对齐 ``delivery_download.py``）。"""
    _kind_map = {
        "manifest": "manifest",
        "preview_xls": "preview_xls",
        "review_workbook": "review_workbook",
    }
    kind = _kind_map.get((artifact_kind or "").strip())
    if kind is None:
        raise JiraHandoffError(
            400,
            "handoff_invalid_artifact_kind",
            f"unsupported artifact_kind: {artifact_kind!r}",
        )
    bundle_dir, manifest = _load_manifest_strict(handoff_id, storage_root=storage_root)
    resolved_bundle = bundle_dir.resolve(strict=True)

    if kind == "manifest":
        target = bundle_dir / "manifest.json"
        return target.resolve(strict=True), f"{bundle_dir.name}-manifest.json", "application/json"

    build_entries = [b for b in (manifest.get("builds") or []) if isinstance(b, dict)]
    chosen: Optional[dict[str, Any]] = None
    if build:
        target_build = build.strip()
        for entry in build_entries:
            if entry.get("build") == target_build or entry.get("build_slug") == target_build:
                chosen = entry
                break
        if chosen is None:
            raise JiraHandoffError(404, "handoff_build_not_found", f"build not found in bundle: {target_build}")
    elif len(build_entries) == 1:
        chosen = build_entries[0]
    else:
        raise JiraHandoffError(
            400,
            "handoff_build_required",
            "build query parameter is required when bundle contains multiple builds",
        )

    art = (chosen.get("artifacts") or {}).get(kind)
    if not isinstance(art, dict) or not art.get("rel_path"):
        raise JiraHandoffError(404, "handoff_artifact_not_found", f"artifact {kind!r} missing in manifest")

    rel_path = str(art["rel_path"])
    rel = Path(rel_path)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise JiraHandoffError(400, "handoff_path_escape_rejected", "invalid artifact relative path")
    expected = resolved_bundle.joinpath(*rel.parts)
    candidate = bundle_dir.joinpath(*rel.parts)
    if not candidate.exists() or candidate.is_symlink():
        raise JiraHandoffError(404, "handoff_artifact_not_found", f"artifact file missing: {rel_path}")
    resolved = candidate.resolve(strict=True)
    if resolved != expected or not resolved.is_relative_to(resolved_bundle):
        raise JiraHandoffError(400, "handoff_path_escape_rejected", "artifact path escaped bundle directory")

    filename = str(art.get("filename") or resolved.name)
    return resolved, filename, "application/vnd.ms-excel"


__all__ = [
    "HANDOFF_DOWNLOAD_KINDS",
    "HANDOFF_MANIFEST_SCHEMA",
    "HANDOFF_MANIFEST_SCHEMA_VERSION",
    "JiraHandoffError",
    "compute_manifest_sha256",
    "create_handoff_bundle",
    "get_handoff_bundle",
    "list_handoff_bundles",
    "resolve_handoff_download_file",
    "run_handoff_preflight",
    "validate_handoff_id",
    "validate_platform",
]
