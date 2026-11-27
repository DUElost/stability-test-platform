"""B11-U1（#4199 v1.1 / #4174）阶段 1：A 段只读提单表单生成引擎与冻结产物存储。

职责边界：
- 仅运行控制面轻量 ``start_log_scan.py -dedup_org``（校验 ``release.json >= 2.1.1``），
  且每次调用在隔离临时目录运行轻量副本（仅只读引用原 ``venv``，绝不将 ``config/*`` 软链回
  原发布目录，确保原发布目录零写入、零截断）；严禁调用 ``-m`` / ``-m 0`` / ``aee_extract``。
- 对工具产物（``*_members.json`` 与配对终表 ``.xls``）执行与 ``scan_membership.py:72-109``
  同等的强契约校验（``schema`` / ``schema_version`` / 输入 SHA256 / 逐行 ``path`` / ``branch`` /
  分组守恒 / 终表完整性），任何不符均 fail-closed 转结构化 ``JiraHandoffError``。
- 覆盖率门禁为硬阻断（无缺主机放行旁路）；全未绑定（0 有效事件）在预检与 422 中返回完整
  ``unbound_rows`` 明细。
- 仅落盘中心存储受控目录 ``_meta/handoff/<handoff_id>/``（校验受控根及其祖先无 symlink，
  零 15.4 外部写入、零 DB 迁移），并经既有 ``record_audit`` 留痕（ADR-0015）。
- 所有 A 段产物恒标 ``status="PENDING_EXPORT"``，预览 ``.xls`` 的 ``Path`` 列写显式
  ``PENDING_EXPORT://...`` 占位，绝不伪造未落盘的厂商共享路径。
- 不导入 FastAPI（C2 分层约束）；路由层负责将 ``JiraHandoffError`` 翻译为 HTTP 响应。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Mapping, Optional, Sequence
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
_SEMVER_PREFIX_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
_MIN_SCAN_TOOL_VERSION = (2, 1, 1)
_DEDUP_ORG_TIMEOUT_SECONDS = 300
_PENDING_EXPORT_PATH_PREFIX = "PENDING_EXPORT://"

_ALLOWED_MEMBER_BRANCHES = frozenset({"filtered", "singleton", "similarity"})
_REQUIRED_FINAL_XLS_COLUMNS = frozenset(
    {"path", "exptime", "expclass", "exptype", "curprocess", "package", "causedby"}
)
_TOOL_COPY_EXCLUDE_TOP = frozenset({"venv", ".venv", "__pycache__", ".git", "logs", "tmp"})
_FILE_READ_NOFOLLOW_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


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
    """一期仅支持字面 ``'mtk'``（#4199 v1.1 §3 B11-U1 ⑧），非字面 ``'mtk'`` 一律 400。"""
    if not isinstance(platform, str) or platform != "mtk":
        raise JiraHandoffError(
            status_code=400,
            code="handoff_unsupported_platform",
            detail=f"platform {platform!r} is not supported in B11 stage 1 (only literal 'mtk' is allowed)",
        )
    return platform


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
    return slug[:80]


def _unique_build_slug(build: str, used_slugs: set[str]) -> str:
    """生成带确定性短哈希的唯一 build slug，防止不同 build 规范化碰撞相互覆写。"""
    base = _build_slug(build)
    digest = hashlib.sha256(build.encode("utf-8")).hexdigest()
    candidate = f"{base}_{digest[:8]}"
    if candidate in used_slugs:
        candidate = f"{base}_{digest[:16]}"
    used_slugs.add(candidate)
    return candidate


def _assert_no_symlink_in_ancestors(path: Path, *, label: str) -> None:
    """保留原始词法路径检查自身与全部祖先，拒绝任何符号链接。"""
    cur = path
    while True:
        try:
            if cur.is_symlink():
                raise JiraHandoffError(
                    status_code=400,
                    code="handoff_symlink_rejected",
                    detail=f"{label} must not contain symlink ancestor: {cur}",
                )
        except OSError as exc:
            raise JiraHandoffError(
                status_code=500,
                code="handoff_storage_io_error",
                detail=f"failed to inspect {label}: {exc}",
            ) from exc
        parent = cur.parent
        if parent == cur:
            break
        cur = parent


def _resolve_handoff_base_dir(storage_root: Optional[str] = None) -> Path:
    """以配置中心根为信任锚定位 ``_meta/handoff``，拒绝根及祖先任何 symlink。"""
    raw_root = (storage_root or "").strip() or resolve_shared_storage_root()
    if not raw_root:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_storage_unconfigured",
            detail="shared storage root (STP_AEE_NFS_ROOT) is not configured",
        )
    anchor = Path(raw_root)
    if not anchor.is_absolute() or ".." in anchor.parts:
        raise JiraHandoffError(
            status_code=400,
            code="handoff_invalid_root",
            detail="shared storage root must be a normalized absolute path",
        )
    _assert_no_symlink_in_ancestors(anchor, label="controlled storage root")
    if anchor.exists() and not anchor.is_dir():
        raise JiraHandoffError(
            status_code=400,
            code="handoff_invalid_root",
            detail="controlled storage root is not a directory",
        )

    cur = anchor
    for seg in ("_meta", "handoff"):
        cur = cur / seg
        try:
            if cur.is_symlink():
                raise JiraHandoffError(
                    status_code=400,
                    code="handoff_symlink_rejected",
                    detail=f"handoff root component must not be a symlink: {cur}",
                )
            if cur.exists() and not cur.is_dir():
                raise JiraHandoffError(
                    status_code=400,
                    code="handoff_invalid_root",
                    detail=f"handoff root component is not a directory: {cur}",
                )
        except OSError as exc:
            raise JiraHandoffError(
                status_code=500,
                code="handoff_storage_io_error",
                detail=f"failed to inspect handoff directory: {exc}",
            ) from exc

    if cur.exists():
        try:
            resolved_anchor = anchor.resolve(strict=True)
            resolved_base = cur.resolve(strict=True)
        except OSError as exc:
            raise JiraHandoffError(
                status_code=500,
                code="handoff_storage_io_error",
                detail=f"failed to resolve handoff directory: {exc}",
            ) from exc
        if resolved_base != resolved_anchor / "_meta" / "handoff":
            raise JiraHandoffError(
                status_code=400,
                code="handoff_symlink_rejected",
                detail="handoff directory resolved away from controlled storage root",
            )
    return cur


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


def _validate_scan_tool_release(script_path: Path) -> dict[str, str]:
    """校验发布目录的 ``release.json`` 身份（要求存在非空 version/git_commit 且版本 >= 2.1.1）。"""
    release_path = script_path.with_name("release.json")
    try:
        st = os.lstat(release_path)
    except FileNotFoundError as exc:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_release_invalid",
            detail="published scan tool release.json is missing",
        ) from exc
    except OSError as exc:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_release_invalid",
            detail=f"failed to stat scan tool release.json: {exc}",
        ) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_release_invalid",
            detail="published scan tool release.json must be a regular file",
        )
    try:
        release = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_release_invalid",
            detail=f"failed to parse scan tool release.json: {exc}",
        ) from exc
    if not isinstance(release, dict):
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_release_invalid",
            detail="scan tool release.json must be a JSON object",
        )
    version = str(release.get("version") or "").strip()
    git_commit = str(release.get("git_commit") or "").strip()
    if not version or not git_commit:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_release_invalid",
            detail="scan tool release.json must contain non-empty version and git_commit",
        )
    match = _SEMVER_PREFIX_RE.match(version)
    if not match:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_release_invalid",
            detail=f"scan tool release version is not parseable semver: {version!r}",
        )
    ver_tuple = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if ver_tuple < _MIN_SCAN_TOOL_VERSION:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_release_invalid",
            detail=f"scan tool version {version!r} is older than required 2.1.1",
        )
    return {"version": version, "git_commit": git_commit}


def _copy_isolated_tool_tree(src_tool_dir: Path, isolated_dir: Path) -> None:
    """将发布工具轻量运行树复制到本次隔离目录（排除 venv/logs/tmp，且禁止软链回原目录）。"""
    isolated_dir.mkdir(parents=True, exist_ok=True)
    for entry in src_tool_dir.iterdir():
        if entry.name in _TOOL_COPY_EXCLUDE_TOP:
            continue
        target = isolated_dir / entry.name
        if entry.is_dir():
            shutil.copytree(
                entry,
                target,
                symlinks=False,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
            )
        elif entry.is_file():
            shutil.copy2(entry, target, follow_symlinks=True)
    (isolated_dir / "logs").mkdir(exist_ok=True)
    (isolated_dir / "tmp").mkdir(exist_ok=True)


def _write_build_org_input_xls(
    dest: Path,
    *,
    build: str,
    events: Sequence[DedupedEvent],
    folder_hint: str,
) -> list[str]:
    """为单个 build 构造规范 15 列 ``<build>_org.xls``，返回逐行写入的 Path 列表供强核验。"""
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("aeeexp", cell_overwrite_ok=True)
    for col_idx, name in enumerate(ORG_COLUMNS):
        ws.write(0, col_idx, name)
    written_paths: list[str] = []
    for row_idx, ev in enumerate(events, start=1):
        synthetic_path = (
            f"{_PENDING_EXPORT_PATH_PREFIX}{folder_hint}/{ev.serial}/aee_exp/{ev.entry}/"
        )
        safe_path = str(safe_xls_cell(synthetic_path))
        written_paths.append(safe_path)
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
    return written_paths


def _read_dedup_final_xls(path: Path) -> list[dict[str, str]]:
    """严格读取并校验 ``-dedup_org`` 产出的终表 ``.xls``（损坏/缺表头/缺必需列一律报错）。"""
    book = xlrd.open_workbook(str(path), on_demand=True)
    try:
        if "aeeexp" not in book.sheet_names():
            raise ValueError(f"final dedup xls missing 'aeeexp' sheet: {path.name}")
        sheet = book.sheet_by_name("aeeexp")
        if sheet.nrows < 1:
            raise ValueError(f"final dedup xls has empty 'aeeexp' sheet: {path.name}")
        headers = [str(v).strip() for v in sheet.row_values(0)]
        col_map = {h.lower(): i for i, h in enumerate(headers) if h}
        missing = sorted(_REQUIRED_FINAL_XLS_COLUMNS - set(col_map))
        if missing:
            raise ValueError(
                f"final dedup xls missing required columns ({', '.join(missing)}): {path.name}"
            )
        if sheet.nrows == 1:
            return []

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
                    "Path": _val(vals, "Path"),
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
) -> tuple[list[dict[str, str]], dict[str, Any], str, str, dict[str, str]]:
    """在每请求只读隔离副本中运行 ``start_log_scan.py -dedup_org`` 并校验产物完整性。

    返回 ``(final_rows, members_data, input_org_sha256, final_xls_sha256, release_info)``。
    原发布工具目录（含 ``config/blacklist.txt``、``logs/``、``tmp/``）全程只读、零写入。
    """
    python_bin = str(tool.get("python") or "").strip()
    script_path = Path(str(tool.get("script") or "").strip())
    if not python_bin or not script_path.is_file():
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_unavailable",
            detail="backend dedup scan tool is not configured or script file missing",
        )
    release_info = _validate_scan_tool_release(script_path)
    input_org_sha256 = hashlib.sha256(org_xls.read_bytes()).hexdigest()

    with tempfile.TemporaryDirectory(prefix="stp-handoff-tool-") as tool_work:
        isolated_dir = Path(tool_work) / "tool"
        try:
            _copy_isolated_tool_tree(script_path.parent, isolated_dir)
        except OSError as exc:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_tool_isolation_failed",
                detail=f"failed to stage isolated scan tool copy: {exc}",
            ) from exc

        isolated_script = isolated_dir / script_path.name
        if not isolated_script.is_file():
            raise JiraHandoffError(
                status_code=502,
                code="handoff_tool_isolation_failed",
                detail=f"isolated scan tool script missing: {script_path.name}",
            )

        argv = [
            python_bin,
            str(isolated_script),
            "-dedup_org",
            str(org_xls),
            "-side",
            side,
        ]
        child_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run(
                argv,
                cwd=str(isolated_dir),
                env=child_env,
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
            detail="-dedup_org did not produce *_members.json (requires start-log-scan >= 2.1.1)",
        )
    if len(members_files) != 1:
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail=f"expected exactly 1 *_members.json, found {len(members_files)}",
        )
    members_path = members_files[0]
    try:
        members_data = json.loads(members_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail=f"failed to parse *_members.json: {exc}",
        ) from exc
    if not isinstance(members_data, dict):
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail="*_members.json root must be a JSON object",
        )

    expected_final_xls = members_path.with_name(
        members_path.name.removesuffix("_members.json") + ".xls"
    )
    if expected_final_xls.is_symlink() or not expected_final_xls.is_file():
        raise JiraHandoffError(
            status_code=502,
            code="handoff_dedup_xls_missing",
            detail=f"paired final dedup xls missing: {expected_final_xls.name}",
        )
    try:
        final_xls_sha256 = hashlib.sha256(expected_final_xls.read_bytes()).hexdigest()
        final_rows = _read_dedup_final_xls(expected_final_xls)
    except (OSError, ValueError, KeyError, TypeError, IndexError, xlrd.XLRDError) as exc:
        raise JiraHandoffError(
            status_code=502,
            code="handoff_dedup_xls_invalid",
            detail=f"failed to read paired final dedup xls: {exc}",
        ) from exc

    return final_rows, members_data, input_org_sha256, final_xls_sha256, release_info


def _is_strict_int(val: Any) -> bool:
    return type(val) is int


def _validate_and_aggregate_groups(
    *,
    build: str,
    events: Sequence[DedupedEvent],
    expected_org_paths: Sequence[str],
    input_org_sha256: str,
    final_xls_sha256: str,
    final_rows: Sequence[Mapping[str, str]],
    members_data: Mapping[str, Any],
    folder_hint: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """按 ``scan_membership.py:72-109`` 同等强校验核验 ``*_members.json`` 与终表，再重算计数。"""
    if members_data.get("schema") != "stp.scan_membership":
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail=f"unsupported *_members.json schema: {members_data.get('schema')!r}",
        )
    schema_ver = members_data.get("schema_version")
    if not isinstance(schema_ver, (str, int, float)) or str(schema_ver).split(".")[0] != "1":
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail=f"unsupported *_members.json schema_version: {schema_ver!r}",
        )

    # 强制绑定输入 _org.xls SHA256（与 scan_membership.py:76 一致）
    meta = members_data.get("meta")
    reports_meta = meta.get("reports") if isinstance(meta, dict) else None
    org_meta = reports_meta.get("org_xls") if isinstance(reports_meta, dict) else None
    recorded_org_sha = org_meta.get("sha256") if isinstance(org_meta, dict) else None
    if not isinstance(recorded_org_sha, str) or recorded_org_sha != input_org_sha256:
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_sha256_mismatch",
            detail="*_members.json meta.reports.org_xls.sha256 missing or mismatches input _org.xls",
        )
    # 若 sidecar 同时登记终表 SHA256，则一并强绑定
    if isinstance(reports_meta, dict):
        for final_key in ("final_xls", "dedup_xls", "result_xls"):
            fmeta = reports_meta.get(final_key)
            if isinstance(fmeta, dict) and "sha256" in fmeta:
                if fmeta.get("sha256") != final_xls_sha256:
                    raise JiraHandoffError(
                        status_code=502,
                        code="handoff_members_sha256_mismatch",
                        detail=f"*_members.json meta.reports.{final_key}.sha256 mismatches paired final xls",
                    )

    rows = members_data.get("rows")
    groups_raw = members_data.get("groups")
    if not isinstance(rows, list) or not isinstance(groups_raw, list):
        raise JiraHandoffError(
            status_code=502,
            code="handoff_members_json_invalid",
            detail="*_members.json must contain list 'rows' and list 'groups'",
        )
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
        if not isinstance(m, dict):
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail="*_members.json row entry must be an object",
            )
        org_row = m.get("org_row")
        if not _is_strict_int(org_row) or org_row < 1 or org_row > len(events):
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"*_members.json row has invalid org_row: {org_row!r}",
            )
        if org_row in by_org_row:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"*_members.json has duplicate org_row: {org_row}",
            )
        branch = m.get("branch")
        if not isinstance(branch, str) or branch not in _ALLOWED_MEMBER_BRANCHES:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"*_members.json row {org_row} has invalid branch: {branch!r}",
            )
        row_path = m.get("path")
        if not isinstance(row_path, str) or row_path != expected_org_paths[org_row - 1]:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_path_mismatch",
                detail=f"*_members.json row {org_row} path mismatches input _org.xls",
            )
        by_org_row[org_row] = m
        if branch == "filtered":
            if m.get("group") is not None:
                raise JiraHandoffError(
                    status_code=502,
                    code="handoff_members_json_invalid",
                    detail=f"*_members.json filtered row {org_row} must not carry a group",
                )
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
    if len(final_rows) != len(groups_raw):
        raise JiraHandoffError(
            status_code=502,
            code="handoff_dedup_xls_invalid",
            detail=f"final dedup xls row count ({len(final_rows)}) != groups count ({len(groups_raw)})",
        )

    covered: set[int] = set()
    seen_final_rows: set[int] = set()
    issues: list[dict[str, Any]] = []
    for idx, g in enumerate(groups_raw, start=1):
        if not isinstance(g, dict):
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail="invalid group entry in *_members.json",
            )
        group_id = g.get("group")
        if group_id is None or isinstance(group_id, bool):
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} missing valid group id",
            )
        raw_members = g.get("member_rows")
        if not isinstance(raw_members, list) or not raw_members:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} missing non-empty member_rows list",
            )
        member_nums: list[int] = []
        for raw_n in raw_members:
            if not _is_strict_int(raw_n) or raw_n < 1 or raw_n > len(events):
                raise JiraHandoffError(
                    status_code=502,
                    code="handoff_members_json_invalid",
                    detail=f"group #{idx} contains invalid member row index: {raw_n!r}",
                )
            member_nums.append(raw_n)
        if len(set(member_nums)) != len(member_nums):
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} contains duplicate member_rows",
            )
        rep_row = g.get("representative_row")
        if not _is_strict_int(rep_row) or rep_row not in member_nums:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} has invalid representative_row: {rep_row!r}",
            )
        if covered.intersection(member_nums):
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} overlaps previous group rows",
            )
        for n in member_nums:
            member_rec = by_org_row[n]
            if member_rec.get("branch") == "filtered" or member_rec.get("group") != group_id:
                raise JiraHandoffError(
                    status_code=502,
                    code="handoff_members_json_invalid",
                    detail=f"group #{idx} member row {n} inconsistent with rows[].group/branch",
                )
        covered.update(member_nums)

        raw_final_row = g.get("final_row")
        if raw_final_row is None:
            final_row_num = idx
        elif _is_strict_int(raw_final_row) and 1 <= raw_final_row <= len(final_rows):
            final_row_num = raw_final_row
        else:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} has invalid final_row: {raw_final_row!r}",
            )
        if final_row_num in seen_final_rows:
            raise JiraHandoffError(
                status_code=502,
                code="handoff_members_json_invalid",
                detail=f"group #{idx} duplicates final_row {final_row_num}",
            )
        seen_final_rows.add(final_row_num)
        final_fields = final_rows[final_row_num - 1]

        member_events = [events[n - 1] for n in member_nums]
        baseline_class = classify_issue_baseline([ev.baseline_class for ev in member_events])
        if baseline_class == "mixed":
            current_members = [ev for ev in member_events if ev.baseline_class == "current"]
            rep_event = current_members[0] if current_members else events[rep_row - 1]
        else:
            rep_event = events[rep_row - 1]

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


def _identity_gate_dict(col: IdentityCollectionResult) -> dict[str, Any]:
    return {
        "total_org_rows": col.total_org_rows,
        "unique_events": len(col.events),
        "keyed_rows": col.keyed_rows,
        "compat_fallback_rows": col.compat_fallback_rows,
        "unbound_rows": len(col.unbound_rows),
        "after_cutoff_skipped_rows": col.after_cutoff_skipped_rows,
        "unbound_by_reason": dict(col.unbound_by_reason),
    }


def run_handoff_preflight(
    db: Session,
    *,
    plan_run_ids: Sequence[int],
    platform: str = "mtk",
    snapshot_cutoff: Optional[datetime] = None,
    storage_root: Optional[str] = None,
) -> dict[str, Any]:
    """A 段只读双门禁预检：零写盘、零外部子进程调用，并暴露完整 ``unbound_rows`` 明细。"""
    norm_platform = validate_platform(platform)
    if not plan_run_ids:
        raise JiraHandoffError(400, "handoff_empty_runs", "plan_run_ids must not be empty")
    try:
        col = collect_handoff_identity(
            db,
            plan_run_ids=plan_run_ids,
            platform=norm_platform,
            snapshot_cutoff=snapshot_cutoff,
            storage_root=storage_root,
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
                "unexpected_hosts": list(r.unexpected_hosts),
                "mapping_status": r.mapping_status,
            }
            for r in col.runs
        ],
        "identity_gate": _identity_gate_dict(col),
        "unbound_rows": [u.to_dict() for u in col.unbound_rows],
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

    # 在读写任何产物前先校验受控存储根及祖先无 symlink
    base_dir = _resolve_handoff_base_dir(storage_root)

    try:
        col = collect_handoff_identity(
            db,
            plan_run_ids=plan_run_ids,
            platform=norm_platform,
            snapshot_cutoff=snapshot_cutoff,
            storage_root=storage_root,
        )
    except LookupError as exc:
        raise JiraHandoffError(404, "handoff_plan_run_not_found", str(exc)) from exc

    # 覆盖率硬门禁：无绕过放行开关（#4199 v1.1 §3② / §4.2①）
    if not col.coverage_complete:
        raise JiraHandoffError(
            status_code=409,
            code="handoff_coverage_incomplete",
            detail="scan coverage gate failed; all expected hosts must have valid reports or empty receipts",
            payload={
                "blocking_issues": list(col.blocking_issues),
                "missing_hosts_by_run": {
                    str(r.plan_run_id): list(r.missing_hosts)
                    for r in col.runs
                    if r.missing_hosts
                },
                "unexpected_hosts_by_run": {
                    str(r.plan_run_id): list(r.unexpected_hosts)
                    for r in col.runs
                    if r.unexpected_hosts
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
            payload={
                "identity_gate": _identity_gate_dict(col),
                "unbound_rows": [u.to_dict() for u in col.unbound_rows],
            },
        )

    tool = dict(scan_tool) if scan_tool is not None else (resolve_scan_tool() or {})
    if not tool.get("python") or not tool.get("script"):
        raise JiraHandoffError(
            status_code=503,
            code="handoff_scan_tool_unavailable",
            detail="STP_BACKEND_DEDUP_SCAN_PYTHON / STP_BACKEND_DEDUP_SCAN_SCRIPT not configured",
        )

    now_utc = datetime.now(timezone.utc)
    ts_compact = now_utc.strftime("%Y%m%d_%H%M%S")
    handoff_id = f"hf_{ts_compact}_{uuid.uuid4().hex[:8]}"
    bundle_dir = base_dir / handoff_id
    try:
        bundle_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise JiraHandoffError(
            status_code=500,
            code="handoff_storage_io_error",
            detail=f"failed to create handoff bundle dir: {exc}",
        ) from exc

    side_env = os.getenv("STP_DEDUP_SCAN_TAG", "shanghai")
    side = "factory" if "factory" in side_env.lower() else "shanghai"
    clean_folder_hint = _build_slug(target_folder_hint or "") if target_folder_hint else ""

    build_entries: list[dict[str, Any]] = []
    used_slugs: set[str] = set()
    tool_release_info: Optional[dict[str, str]] = None
    manifest_written = False
    try:
        for build_name in sorted(events_by_build):
            build_events = events_by_build[build_name]
            slug = _unique_build_slug(build_name, used_slugs)
            folder_label = clean_folder_hint or slug
            with tempfile.TemporaryDirectory(prefix=f"stp-handoff-{slug[:32]}-") as tmpdir:
                tmp_root = Path(tmpdir)
                org_input = tmp_root / f"{slug}_org.xls"
                written_paths = _write_build_org_input_xls(
                    org_input,
                    build=build_name,
                    events=build_events,
                    folder_hint=folder_label,
                )
                (
                    final_rows,
                    members_data,
                    input_org_sha256,
                    final_xls_sha256,
                    release_info,
                ) = _invoke_dedup_org_tool(tool, org_input, side=side)
                tool_release_info = release_info

            issues, filtered_rows, filtered_by_reason = _validate_and_aggregate_groups(
                build=build_name,
                events=build_events,
                expected_org_paths=written_paths,
                input_org_sha256=input_org_sha256,
                final_xls_sha256=final_xls_sha256,
                final_rows=final_rows,
                members_data=members_data,
                folder_hint=folder_label,
            )

            build_dir = bundle_dir / "builds" / slug
            build_dir.mkdir(parents=True, exist_ok=True)
            preview_filename = f"preview_Result_None_None_MonkeyAEE_SH_{slug}_{ts_compact}.xls"
            review_filename = f"review_workbook_{slug}.xls"
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
            "target_folder_hint": clean_folder_hint or None,
            "scan_tool_release": tool_release_info,
            "gates": {
                "coverage_complete": col.coverage_complete,
                "identity_degraded": col.identity_degraded,
                "blocking_issues": list(col.blocking_issues),
                "warnings": list(col.warnings),
            },
            "identity_gate": _identity_gate_dict(col),
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
                    "unexpected_hosts": list(r.unexpected_hosts),
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
            "manifest_sha256": manifest_sha256,
        },
        user_id=user_id,
        username=username,
        request=request,
    )
    db.commit()

    return manifest_body


def _read_regular_file_text_nofollow(path: Path, *, not_found_code: str, not_found_msg: str) -> str:
    """用 ``O_NOFOLLOW`` 打开并核验普通文件后读取 UTF-8 文本，统一捕获权限与文件类型异常。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError as exc:
        raise JiraHandoffError(404, not_found_code, not_found_msg) from exc
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot stat file: {exc}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise JiraHandoffError(400, "handoff_symlink_rejected", "handoff file path must not follow symlinks")
    if not stat.S_ISREG(st.st_mode):
        raise JiraHandoffError(400, "handoff_not_regular_file", "handoff file path must be a regular file")
    try:
        fd = os.open(str(path), _FILE_READ_NOFOLLOW_FLAGS)
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot open file: {exc}") from exc
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise JiraHandoffError(400, "handoff_not_regular_file", "handoff file path must be a regular file")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            return fh.read()
    except JiraHandoffError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise JiraHandoffError(500, "handoff_manifest_corrupted", f"failed to read file: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _find_bundle_dir_untainted(base_dir: Path, clean_id: str) -> Path:
    """从受控根目录直接枚举定位 bundle 目录，切断用户输入直接拼接路径的污点链。"""
    if not base_dir.exists():
        raise JiraHandoffError(404, "handoff_bundle_not_found", f"handoff bundle not found: {clean_id}")
    if base_dir.is_symlink() or not base_dir.is_dir():
        raise JiraHandoffError(400, "handoff_symlink_rejected", "handoff root must be a plain directory")
    try:
        entries = list(base_dir.iterdir())
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"failed to list handoff dir: {exc}") from exc
    for child in entries:
        if child.name != clean_id:
            continue
        try:
            st = os.lstat(child)
        except OSError as exc:
            raise JiraHandoffError(500, "handoff_storage_io_error", f"failed to stat bundle dir: {exc}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise JiraHandoffError(400, "handoff_symlink_rejected", "handoff bundle dir must not be a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise JiraHandoffError(400, "handoff_invalid_root", "handoff bundle entry is not a directory")
        return child
    raise JiraHandoffError(404, "handoff_bundle_not_found", f"handoff bundle not found: {clean_id}")


def _validate_manifest_shape(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise JiraHandoffError(500, "handoff_manifest_corrupted", "manifest root must be a JSON object")
    if data.get("schema") != HANDOFF_MANIFEST_SCHEMA:
        raise JiraHandoffError(500, "handoff_manifest_corrupted", "invalid handoff manifest schema")
    required_dict_keys = ("gates", "identity_gate")
    required_list_keys = ("plan_run_ids", "runs", "unbound_rows", "builds")
    required_str_keys = ("schema_version", "handoff_id", "status", "platform", "created_at", "manifest_sha256")
    for k in required_dict_keys:
        if not isinstance(data.get(k), dict):
            raise JiraHandoffError(500, "handoff_manifest_corrupted", f"manifest field {k!r} must be an object")
    for k in required_list_keys:
        if not isinstance(data.get(k), list):
            raise JiraHandoffError(500, "handoff_manifest_corrupted", f"manifest field {k!r} must be a list")
    for k in required_str_keys:
        if not isinstance(data.get(k), str) or not data.get(k):
            raise JiraHandoffError(500, "handoff_manifest_corrupted", f"manifest field {k!r} must be a non-empty string")
    return data


def _load_manifest_strict(handoff_id: str, storage_root: Optional[str] = None) -> tuple[Path, dict[str, Any]]:
    clean_id = validate_handoff_id(handoff_id)
    base_dir = _resolve_handoff_base_dir(storage_root)
    bundle_dir = _find_bundle_dir_untainted(base_dir, clean_id)
    manifest_path = bundle_dir / "manifest.json"
    raw_text = _read_regular_file_text_nofollow(
        manifest_path,
        not_found_code="handoff_bundle_not_found",
        not_found_msg=f"handoff bundle not found: {clean_id}",
    )
    try:
        resolved = manifest_path.resolve(strict=True)
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot resolve manifest: {exc}") from exc
    if resolved != manifest_path:
        raise JiraHandoffError(400, "handoff_symlink_rejected", "handoff manifest path must not follow symlinks")
    try:
        data = json.loads(raw_text)
    except ValueError as exc:
        raise JiraHandoffError(500, "handoff_manifest_corrupted", f"failed to parse manifest: {exc}") from exc
    return bundle_dir, _validate_manifest_shape(data)


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
    if not base_dir.exists():
        return []
    try:
        entries = list(base_dir.iterdir())
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"failed to list handoff bundles: {exc}") from exc
    summaries: list[dict[str, Any]] = []
    for child in entries:
        try:
            st = os.lstat(child)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            continue
        if not _HANDOFF_ID_RE.match(child.name):
            continue
        manifest_path = child / "manifest.json"
        try:
            raw_text = _read_regular_file_text_nofollow(
                manifest_path,
                not_found_code="handoff_bundle_not_found",
                not_found_msg="manifest missing",
            )
            data = _validate_manifest_shape(json.loads(raw_text))
        except (JiraHandoffError, ValueError):
            logger.warning("skipping unreadable or corrupted handoff manifest: %s", child.name)
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
    """按白名单定位 A 段可下载产物，拒绝越根、祖先/叶子符号链接及非普通文件。"""
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
    try:
        resolved_bundle = bundle_dir.resolve(strict=True)
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot resolve bundle dir: {exc}") from exc

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

    # 逐段拒绝 bundle 内中间目录或叶子符号链接
    cursor = bundle_dir
    for part in rel.parts:
        cursor = cursor / part
        try:
            part_st = os.lstat(cursor)
        except FileNotFoundError as exc:
            raise JiraHandoffError(404, "handoff_artifact_not_found", f"artifact file missing: {rel_path}") from exc
        except OSError as exc:
            raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot stat artifact path: {exc}") from exc
        if stat.S_ISLNK(part_st.st_mode):
            raise JiraHandoffError(400, "handoff_symlink_rejected", "artifact path must not follow symlinks")

    expected = resolved_bundle.joinpath(*rel.parts)
    candidate = bundle_dir.joinpath(*rel.parts)
    try:
        leaf_st = os.lstat(candidate)
    except FileNotFoundError as exc:
        raise JiraHandoffError(404, "handoff_artifact_not_found", f"artifact file missing: {rel_path}") from exc
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot stat artifact: {exc}") from exc
    if stat.S_ISLNK(leaf_st.st_mode):
        raise JiraHandoffError(400, "handoff_symlink_rejected", "artifact path must not follow symlinks")
    if not stat.S_ISREG(leaf_st.st_mode):
        raise JiraHandoffError(400, "handoff_not_regular_file", "artifact path must be a regular file")

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot resolve artifact: {exc}") from exc
    if resolved != expected or not resolved.is_relative_to(resolved_bundle):
        raise JiraHandoffError(400, "handoff_path_escape_rejected", "artifact path escaped bundle directory")

    # 打开验证普通文件可读（捕获权限拒绝，避免 500 裸异常）
    try:
        fd = os.open(str(resolved), _FILE_READ_NOFOLLOW_FLAGS)
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_artifact_unreadable", f"artifact file is unreadable: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise JiraHandoffError(400, "handoff_not_regular_file", "artifact path must be a regular file")
    finally:
        os.close(fd)

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
