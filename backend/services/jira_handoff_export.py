"""B11-U3（#4199 v1.1 / #4174）阶段 3a：按冻结 manifest 受控导出至 15.4 + 过渡期单并发补解。

职责边界：
- 唯一输入为已冻结 ``manifest.json``；执行前重算 ``manifest_sha256`` 并与请求期望值强比对；
- 人工显式 ``target_folder_name`` + ``merge_strategy``，拒绝路径穿越 / 内网 IP / symlink；
- 代表目录不覆盖：已存在且字节不一致 fail-closed；一致则幂等跳过；
- 过渡期缺失 ``*.DEC/__exp_main.txt`` 时，仅对选定子集在隔离临时目录补解，受跨进程
  全局 ``flock``（并发硬上限 = 1）+ ``ionice -c 3`` 保护；绝不改写原中心存储或原发布工具目录；
- 全部 ``Path`` 映射回本机导出根 ``isfile`` 为真后才原子发布终稿 ``.xls`` 并写入
  ``export_receipt.json`` 投影 ``EXPORTED``；原 ``manifest.json`` 保持不可变。
"""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
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
import threading
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy.orm import Session
import xlwt

from backend.agent.contracts.evidence_layout import EvidenceLayoutError, copytree_no_symlinks
from backend.core.settings.jira_handoff_export import (
    get_jira_handoff_export_settings,
    reset_jira_handoff_export_settings_cache,
)
from backend.core.storage_root import resolve_shared_storage_root
from backend.services.audit_writer import record_audit
from backend.services.dedup_scan import resolve_scan_tool
from backend.services.jira_handoff_builder import (
    JiraHandoffError,
    _assert_no_symlink_in_ancestors,
    _atomic_write_bytes,
    _canonical_json_bytes,
    _load_manifest_strict,
    compute_manifest_sha256,
    validate_handoff_id,
)
from backend.services.jira_handoff_identity import FORM_COLUMNS, safe_xls_cell

logger = logging.getLogger(__name__)

HANDOFF_STATUS_EXPORTED = "EXPORTED"
EXPORT_RECEIPT_SCHEMA = "stp.jira_handoff_export_receipt"
EXPORT_RECEIPT_SCHEMA_VERSION = "1.0"
EXPORT_RECEIPT_FILENAME = "export_receipt.json"

_MERGE_STRATEGIES = frozenset({"create_new", "merge_existing"})
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
# 目录名内任意位置子串命中即拒（含被 _/- 夹住的脱敏失败形态）。
_PRIVATE_IP_DOT_RE = re.compile(
    r"(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})"
)
_PRIVATE_IP_DASH_RE = re.compile(
    r"(?:10-\d{1,3}-\d{1,3}-\d{1,3}"
    r"|172-(?:1[6-9]|2\d|3[01])-\d{1,3}-\d{1,3}"
    r"|192-168-\d{1,3}-\d{1,3})"
)
_SAFE_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DEC_EXTRACT_TIMEOUT_SECONDS = 300
_ACTIVE_EXTRACT_COUNT = 0
_ACTIVE_EXTRACT_LOCK = threading.Lock()
_ACTIVE_EXTRACT_PEAK = 0

# 测试可注入：替换真实 aee_extract 调用（保持全局锁路径不变）。
_EXTRACT_HOOK: Optional[Any] = None


def _settings():
    return get_jira_handoff_export_settings()


def reset_export_runtime_state_for_tests() -> None:
    """测试夹具：清 Settings 缓存与补解并发计数峰值。"""
    global _ACTIVE_EXTRACT_COUNT, _ACTIVE_EXTRACT_PEAK, _EXTRACT_HOOK
    reset_jira_handoff_export_settings_cache()
    with _ACTIVE_EXTRACT_LOCK:
        _ACTIVE_EXTRACT_COUNT = 0
        _ACTIVE_EXTRACT_PEAK = 0
    _EXTRACT_HOOK = None


def set_extract_hook_for_tests(hook: Optional[Any]) -> None:
    """测试夹具：注入补解实现（签名 ``(dbg_path: Path, work_dir: Path) -> Path``）。"""
    global _EXTRACT_HOOK
    _EXTRACT_HOOK = hook


def get_active_extract_peak_for_tests() -> int:
    with _ACTIVE_EXTRACT_LOCK:
        return _ACTIVE_EXTRACT_PEAK


def validate_target_folder_name(name: str) -> str:
    """校验人工显式目标目录名：禁穿越、控制字符、内网 IP、空白与特殊分隔符。"""
    if not isinstance(name, str):
        raise JiraHandoffError(400, "handoff_invalid_target_folder", "target_folder_name must be a string")
    cleaned = name.strip()
    if not cleaned:
        raise JiraHandoffError(400, "handoff_invalid_target_folder", "target_folder_name must not be empty")
    if cleaned != name:
        raise JiraHandoffError(
            400,
            "handoff_invalid_target_folder",
            "target_folder_name must not have leading/trailing whitespace",
        )
    if "/" in cleaned or "\\" in cleaned or ".." in cleaned:
        raise JiraHandoffError(
            400,
            "handoff_invalid_target_folder",
            "target_folder_name must not contain path separators or '..'",
        )
    if _CONTROL_CHAR_RE.search(cleaned):
        raise JiraHandoffError(
            400,
            "handoff_invalid_target_folder",
            "target_folder_name must not contain control characters",
        )
    if _PRIVATE_IP_DOT_RE.search(cleaned) or _PRIVATE_IP_DASH_RE.search(cleaned):
        raise JiraHandoffError(
            400,
            "handoff_invalid_target_folder",
            "target_folder_name must not embed private/intranet IP literals",
        )
    if not _SAFE_FOLDER_RE.match(cleaned):
        raise JiraHandoffError(
            400,
            "handoff_invalid_target_folder",
            "target_folder_name must be a single safe path segment",
        )
    return cleaned


def validate_merge_strategy(strategy: str) -> str:
    if not isinstance(strategy, str) or strategy not in _MERGE_STRATEGIES:
        raise JiraHandoffError(
            400,
            "handoff_invalid_merge_strategy",
            "merge_strategy must be literal 'create_new' or 'merge_existing'",
        )
    return strategy


def resolve_export_root(*, export_root: Optional[str] = None) -> Path:
    raw = (export_root if export_root is not None else _settings().stp_jira_handoff_export_root).strip()
    if not raw:
        raise JiraHandoffError(503, "handoff_export_root_unconfigured", "export root is not configured")
    root = Path(raw)
    if not root.is_absolute() or ".." in root.parts:
        raise JiraHandoffError(
            400,
            "handoff_invalid_export_root",
            "export root must be a normalized absolute path",
        )
    _assert_no_symlink_in_ancestors(root, label="jira handoff export root")
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise JiraHandoffError(
                400,
                "handoff_invalid_export_root",
                "export root must be a plain directory without symlink",
            )
    return root


def vendor_path_prefix(*, prefix: Optional[str] = None) -> str:
    raw = (prefix if prefix is not None else _settings().stp_jira_handoff_vendor_path_prefix).strip()
    if not raw or not raw.startswith("/") or ".." in Path(raw).parts:
        raise JiraHandoffError(
            503,
            "handoff_vendor_prefix_invalid",
            "vendor path prefix must be an absolute normalized path",
        )
    return raw.rstrip("/")


def map_vendor_path_to_local(vendor_path: str, *, export_root: Path, vendor_prefix: str) -> Path:
    if not vendor_path.startswith(vendor_prefix + "/") and vendor_path != vendor_prefix:
        raise JiraHandoffError(
            500,
            "handoff_vendor_path_unmapped",
            f"form Path is outside vendor prefix: {vendor_path!r}",
        )
    rel = vendor_path[len(vendor_prefix) :].lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise JiraHandoffError(500, "handoff_vendor_path_unmapped", "form Path relative segment is invalid")
    return export_root / rel


def verify_frozen_manifest(manifest: Mapping[str, Any], expected_sha256: str) -> str:
    """重算 digest 并与 manifest 内嵌值、请求期望值三方强比对。"""
    if not isinstance(expected_sha256, str) or not expected_sha256.strip():
        raise JiraHandoffError(400, "handoff_manifest_sha256_required", "manifest_sha256 is required")
    want = expected_sha256.strip().lower()
    embedded = str(manifest.get("manifest_sha256") or "").strip().lower()
    recomputed = compute_manifest_sha256(manifest).lower()
    if not embedded or embedded != recomputed:
        raise JiraHandoffError(
            409,
            "handoff_manifest_tampered",
            "frozen manifest_sha256 does not match recomputed digest",
            payload={"embedded": embedded, "recomputed": recomputed},
        )
    if want != recomputed:
        raise JiraHandoffError(
            409,
            "handoff_manifest_sha256_mismatch",
            "request manifest_sha256 does not match frozen manifest digest",
            payload={"expected": want, "actual": recomputed},
        )
    return recomputed


def load_export_receipt(bundle_dir: Path) -> Optional[dict[str, Any]]:
    path = bundle_dir / EXPORT_RECEIPT_FILENAME
    if not path.exists():
        return None
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot stat export receipt: {exc}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise JiraHandoffError(400, "handoff_symlink_rejected", "export receipt must be a regular file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JiraHandoffError(500, "handoff_export_receipt_corrupted", f"failed to read receipt: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != EXPORT_RECEIPT_SCHEMA:
        raise JiraHandoffError(500, "handoff_export_receipt_corrupted", "invalid export receipt schema")
    return data


def project_bundle_status(manifest: Mapping[str, Any], bundle_dir: Path) -> str:
    receipt = load_export_receipt(bundle_dir)
    if receipt and str(receipt.get("status") or "") == HANDOFF_STATUS_EXPORTED:
        return HANDOFF_STATUS_EXPORTED
    return str(manifest.get("status") or "")


def _inventory_non_dec_files(root: Path) -> dict[str, tuple[int, str]]:
    """返回相对路径 → (size, sha256)，排除 ``*.DEC`` 目录树。"""
    out: dict[str, tuple[int, str]] = {}
    if not root.exists():
        return out
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        cur = Path(current)
        dirs[:] = [d for d in dirs if not d.endswith(".DEC") and not (cur / d).is_symlink()]
        for name in files:
            fp = cur / name
            if fp.is_symlink() or not fp.is_file():
                continue
            rel = fp.relative_to(root).as_posix()
            data = fp.read_bytes()
            out[rel] = (len(data), hashlib.sha256(data).hexdigest())
    return out


def _find_dec_exp_main(entry_dir: Path) -> Optional[Path]:
    if not entry_dir.is_dir():
        return None
    matches: list[Path] = []
    try:
        for child in entry_dir.iterdir():
            if child.is_symlink():
                continue
            if child.is_dir() and child.name.endswith(".DEC"):
                cand = child / "__exp_main.txt"
                if cand.is_file() and not cand.is_symlink():
                    matches.append(cand)
    except OSError:
        return None
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_center_source(
    evidence_location: str,
    *,
    storage_root: Path,
) -> Path:
    raw = (evidence_location or "").strip()
    if not raw:
        raise JiraHandoffError(
            422,
            "handoff_evidence_unavailable",
            "representative evidence_location is empty; center storage source required for export",
        )
    src = Path(raw)
    if not src.is_absolute():
        src = storage_root / raw
    try:
        resolved_root = storage_root.resolve(strict=True)
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_storage_io_error", f"cannot resolve storage root: {exc}") from exc
    _assert_no_symlink_in_ancestors(src, label="evidence source")
    if src.is_symlink() or not src.is_dir():
        raise JiraHandoffError(
            422,
            "handoff_evidence_unavailable",
            f"evidence source must be a plain directory under controlled storage: {src}",
        )
    try:
        resolved_src = src.resolve(strict=True)
    except OSError as exc:
        raise JiraHandoffError(422, "handoff_evidence_unavailable", f"cannot resolve evidence source: {exc}") from exc
    try:
        resolved_src.relative_to(resolved_root)
    except ValueError as exc:
        raise JiraHandoffError(
            422,
            "handoff_evidence_outside_root",
            "evidence source escapes controlled storage root",
        ) from exc
    return src


def _copy_representative_dir_safe(src: Path, dest: Path, *, export_root: Path) -> str:
    """不覆盖复制；临时叶子挂在 dest 父目录下，``dest_root=export_root``。"""
    if dest.exists():
        if dest.is_symlink() or not dest.is_dir():
            raise JiraHandoffError(
                409,
                "handoff_export_dest_conflict",
                f"destination exists but is not a plain directory: {dest}",
            )
        src_inv = _inventory_non_dec_files(src)
        dst_inv = _inventory_non_dec_files(dest)
        if src_inv != dst_inv:
            raise JiraHandoffError(
                409,
                "handoff_export_overwrite_rejected",
                "destination event directory already exists with divergent non-.DEC content",
                payload={
                    "dest": str(dest),
                    "src_files": len(src_inv),
                    "dst_files": len(dst_inv),
                },
            )
        return "skipped"

    dest.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_in_ancestors(dest.parent, label="export destination parent")
    tmp_leaf = dest.parent / f".stp-handoff-copy-{os.getpid()}-{threading.get_ident()}.tmp"
    if tmp_leaf.exists():
        shutil.rmtree(tmp_leaf, ignore_errors=True)
    try:
        copytree_no_symlinks(src, tmp_leaf, dest_root=export_root)
        os.replace(tmp_leaf, dest)
    except EvidenceLayoutError as exc:
        shutil.rmtree(tmp_leaf, ignore_errors=True)
        raise JiraHandoffError(500, "handoff_export_copy_failed", f"failed to copy representative: {exc}") from exc
    except FileExistsError as exc:
        shutil.rmtree(tmp_leaf, ignore_errors=True)
        raise JiraHandoffError(409, "handoff_export_dest_conflict", str(exc)) from exc
    except BaseException:
        shutil.rmtree(tmp_leaf, ignore_errors=True)
        raise
    return "copied"


class _DecExtractGlobalLock:
    """跨进程全局补解锁（硬上限 1）。

    不用 ``@contextmanager``：``JiraHandoffError`` 是 frozen dataclass，在
    generator 上下文里抛出时 CPython 会写 ``__traceback__`` 触发 FrozenInstanceError。
    """

    def __init__(self, *, wait: bool = True) -> None:
        self._wait = wait
        self._fd: Optional[int] = None
        self._acquired = False

    def __enter__(self) -> "_DecExtractGlobalLock":
        settings = _settings()
        if int(settings.stp_jira_handoff_dec_extract_max_concurrent) != 1:
            raise JiraHandoffError(
                status_code=503,
                code="handoff_dec_extract_limit_invalid",
                detail="dec extract max concurrent must be exactly 1",
            )
        lock_dir = Path(settings.stp_jira_handoff_dec_extract_lock_dir)
        lock_dir.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_in_ancestors(lock_dir, label="dec extract lock dir")
        lock_path = lock_dir / "global.lock"
        self._fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        flags = fcntl.LOCK_EX if self._wait else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(self._fd, flags)
            self._acquired = True
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise JiraHandoffError(
                status_code=429,
                code="handoff_dec_extract_busy",
                detail="another transitional DEC extract is in progress (global concurrency=1)",
            ) from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._acquired and self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._acquired = False


def _track_extract_enter() -> None:
    global _ACTIVE_EXTRACT_COUNT, _ACTIVE_EXTRACT_PEAK
    with _ACTIVE_EXTRACT_LOCK:
        _ACTIVE_EXTRACT_COUNT += 1
        if _ACTIVE_EXTRACT_COUNT > _ACTIVE_EXTRACT_PEAK:
            _ACTIVE_EXTRACT_PEAK = _ACTIVE_EXTRACT_COUNT


def _track_extract_exit() -> None:
    global _ACTIVE_EXTRACT_COUNT
    with _ACTIVE_EXTRACT_LOCK:
        _ACTIVE_EXTRACT_COUNT = max(0, _ACTIVE_EXTRACT_COUNT - 1)


def resolve_aee_extract_tool(*, override: Optional[str] = None) -> Path:
    if override:
        tool = Path(override)
    else:
        scan = resolve_scan_tool() or {}
        script = scan.get("script")
        if not script:
            raise JiraHandoffError(
                503,
                "handoff_aee_extract_unavailable",
                "STP_BACKEND_DEDUP_SCAN_SCRIPT not configured; cannot locate aee_extract",
            )
        tool = Path(script).resolve().parent / "tools" / "aee_extract" / "aee_extract"
    if not tool.is_file() or tool.is_symlink():
        raise JiraHandoffError(
            503,
            "handoff_aee_extract_unavailable",
            f"aee_extract tool not found or is symlink: {tool}",
        )
    return tool


def _run_aee_extract_isolated(
    dbg_file: Path,
    *,
    tool: Optional[Path],
    work_dir: Path,
) -> Path:
    """在隔离目录对单个 ``.dbg`` 执行补解；返回产出的 ``__exp_main.txt``。"""
    if _EXTRACT_HOOK is not None:
        return _EXTRACT_HOOK(dbg_file, work_dir)
    if tool is None:
        raise JiraHandoffError(
            status_code=503,
            code="handoff_aee_extract_unavailable",
            detail="aee_extract tool path is required when extract hook is unset",
        )

    work_dbg = work_dir / dbg_file.name
    shutil.copyfile(dbg_file, work_dbg)
    cmd = [str(tool), str(work_dbg)]
    ionice = shutil.which("ionice")
    if ionice:
        cmd = [ionice, "-c", "3", *cmd]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            timeout=_DEC_EXTRACT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise JiraHandoffError(
            504,
            "handoff_dec_extract_timeout",
            f"aee_extract timed out for {dbg_file.name}",
        ) from exc
    if proc.returncode != 0:
        raise JiraHandoffError(
            502,
            "handoff_dec_extract_failed",
            f"aee_extract failed for {dbg_file.name} (rc={proc.returncode})",
        )
    # 约定：aee_extract 在同目录产出 <dbg>.DEC/__exp_main.txt
    dec_dir = work_dir / f"{dbg_file.name}.DEC"
    if not dec_dir.is_dir():
        # 有的工具产出去掉 .dbg 后缀的名字；兜底扫描
        found = list(work_dir.glob("*.DEC"))
        if len(found) == 1 and found[0].is_dir():
            dec_dir = found[0]
        else:
            raise JiraHandoffError(
                502,
                "handoff_dec_extract_failed",
                f"aee_extract produced no .DEC directory for {dbg_file.name}",
            )
    exp_main = dec_dir / "__exp_main.txt"
    if not exp_main.is_file() or exp_main.is_symlink():
        raise JiraHandoffError(
            502,
            "handoff_dec_extract_failed",
            f"aee_extract missing __exp_main.txt for {dbg_file.name}",
        )
    return exp_main


def ensure_dec_for_entry(
    entry_dir: Path,
    *,
    wait_for_lock: bool = True,
    aee_extract_tool: Optional[Path] = None,
) -> Path:
    """确保代表目录恰有可读 ``*.DEC/__exp_main.txt``；缺失则过渡期隔离补解。"""
    existing = _find_dec_exp_main(entry_dir)
    if existing is not None:
        return existing

    dbg_files = sorted(
        p for p in entry_dir.glob("*.dbg") if p.is_file() and not p.is_symlink()
    )
    if not dbg_files:
        raise JiraHandoffError(
            422,
            "handoff_dec_missing",
            f"representative lacks *.DEC/__exp_main.txt and has no *.dbg to extract: {entry_dir}",
        )
    # 仅对缺失子集：选一个 .dbg（通常代表目录仅一个主 dbg），绝不扫整批。
    dbg = dbg_files[0]
    tool: Optional[Path]
    if _EXTRACT_HOOK is not None:
        tool = aee_extract_tool
    else:
        tool = aee_extract_tool or resolve_aee_extract_tool()

    with _DecExtractGlobalLock(wait=wait_for_lock):
        # 锁内二次检查，避免排队期间已被其他请求补齐。
        existing = _find_dec_exp_main(entry_dir)
        if existing is not None:
            return existing
        _track_extract_enter()
        try:
            with tempfile.TemporaryDirectory(prefix="stp-handoff-dec-") as tmp:
                work = Path(tmp)
                exp_main = _run_aee_extract_isolated(dbg, tool=tool, work_dir=work)
                dec_src = exp_main.parent
                dest_dec = entry_dir / dec_src.name
                if dest_dec.exists():
                    raise JiraHandoffError(
                        status_code=409,
                        code="handoff_export_overwrite_rejected",
                        detail=f".DEC destination already exists unexpectedly: {dest_dec}",
                    )
                shutil.copytree(dec_src, dest_dec, symlinks=False)
                final = dest_dec / "__exp_main.txt"
                if not final.is_file() or final.is_symlink():
                    raise JiraHandoffError(
                        status_code=502,
                        code="handoff_dec_extract_failed",
                        detail="copied .DEC is missing readable __exp_main.txt",
                    )
                return final
        finally:
            _track_extract_exit()


def _render_final_form_xls(
    dest: Path,
    *,
    build: str,
    folder_name: str,
    issues: Sequence[Mapping[str, Any]],
    vendor_prefix: str,
    export_root: Path,
) -> tuple[str, list[str]]:
    """渲染终稿 15 列表单；同时返回已核验的本机 Path 列表。"""
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("aeeexp", cell_overwrite_ok=True)
    widths = [1000, 10000, 6000, 7000, 2000, 4000, 7000, 7000, 30000, 30000, 7000, 5000, 7000, 10000, 8000]
    for col_idx, width in enumerate(widths):
        ws.col(col_idx).width = width
    st_hdr = xlwt.easyxf("font: height 200, name Times New Roman, color-index blue, bold on")
    st_body = xlwt.easyxf("font: name calibri")
    for col_idx, col_name in enumerate(FORM_COLUMNS):
        ws.write(0, col_idx, col_name, st_hdr)

    verified_local_paths: list[str] = []
    for row_idx, item in enumerate(issues, start=1):
        rep = item["representative"]
        serial = str(rep["serial"])
        entry = str(rep["entry"])
        local_entry = export_root / folder_name / serial / "aee_exp" / entry
        exp_main = _find_dec_exp_main(local_entry)
        if exp_main is None:
            raise JiraHandoffError(
                422,
                "handoff_dec_missing",
                f"representative missing *.DEC/__exp_main.txt before final publish: {serial}/{entry}",
            )
        vendor_path = (
            f"{vendor_prefix}/{folder_name}/{serial}/aee_exp/{entry}/"
            f"{exp_main.parent.name}/__exp_main.txt"
        )
        local_mapped = map_vendor_path_to_local(
            vendor_path, export_root=export_root, vendor_prefix=vendor_prefix
        )
        try:
            st = os.lstat(local_mapped)
        except OSError as exc:
            raise JiraHandoffError(
                422,
                "handoff_path_unreachable",
                f"mapped local Path is not reachable: {local_mapped}",
            ) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise JiraHandoffError(
                422,
                "handoff_path_unreachable",
                f"mapped local Path must be a regular non-symlink file: {local_mapped}",
            )
        if not os.path.isfile(local_mapped):
            raise JiraHandoffError(
                422,
                "handoff_path_unreachable",
                f"mapped local Path isfile=False: {local_mapped}",
            )
        verified_local_paths.append(str(local_mapped))

        ff = item["form_fields"]
        values: list[Any] = [
            row_idx - 1,
            vendor_path,
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
    digest = _atomic_write_bytes(dest, data)
    return digest, verified_local_paths


def _publish_final_xls_with_backup(
    folder_dir: Path,
    *,
    build: str,
    folder_name: str,
    issues: Sequence[Mapping[str, Any]],
    vendor_prefix: str,
    export_root: Path,
    ts_compact: str,
) -> dict[str, Any]:
    filename = f"Result_None_None_MonkeyAEE_SH_{ts_compact}.xls"
    final_path = folder_dir / filename
    # 备份同目录下既有终稿同名族（最新 Result_*.xls）
    backups: list[dict[str, str]] = []
    existing = sorted(folder_dir.glob("Result_None_None_MonkeyAEE_SH_*.xls"))
    for old in existing:
        if old.is_symlink() or not old.is_file():
            continue
        if old.name.endswith(".xls") and ".bak-" in old.name:
            continue
        raw = old.read_bytes()
        old_sha = hashlib.sha256(raw).hexdigest()
        bak_name = f"{old.name}.bak-{ts_compact}"
        bak_path = folder_dir / bak_name
        _atomic_write_bytes(bak_path, raw)
        bak_sha = hashlib.sha256(bak_path.read_bytes()).hexdigest()
        if bak_sha != old_sha:
            raise JiraHandoffError(
                500,
                "handoff_xls_backup_mismatch",
                f"backup checksum mismatch for {old.name}",
            )
        backups.append({"original": old.name, "backup": bak_name, "sha256": old_sha})

    digest, verified = _render_final_form_xls(
        final_path,
        build=build,
        folder_name=folder_name,
        issues=issues,
        vendor_prefix=vendor_prefix,
        export_root=export_root,
    )
    return {
        "filename": filename,
        "sha256": digest,
        "size_bytes": final_path.stat().st_size,
        "backups": backups,
        "verified_local_paths": verified,
    }


def export_handoff_bundle(
    db: Session,
    handoff_id: str,
    *,
    manifest_sha256: str,
    builds: Sequence[Mapping[str, Any]],
    storage_root: Optional[str] = None,
    export_root: Optional[str] = None,
    vendor_prefix: Optional[str] = None,
    wait_for_dec_lock: bool = True,
    aee_extract_tool: Optional[str] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    request: Optional[Any] = None,
) -> dict[str, Any]:
    """执行 B 段受控导出；成功后写入 ``export_receipt.json`` 并投影 ``EXPORTED``。"""
    clean_id = validate_handoff_id(handoff_id)
    bundle_dir, manifest = _load_manifest_strict(clean_id, storage_root=storage_root)
    digest = verify_frozen_manifest(manifest, manifest_sha256)

    if str(manifest.get("status") or "") == HANDOFF_STATUS_EXPORTED or load_export_receipt(bundle_dir):
        # 已导出：仍要求 sha 匹配；直接返回既有回执（幂等）。
        receipt = load_export_receipt(bundle_dir)
        if receipt:
            return receipt

    if not builds:
        raise JiraHandoffError(400, "handoff_export_builds_required", "builds targets are required")

    manifest_builds = {str(b.get("build") or ""): b for b in manifest.get("builds") or []}
    if not manifest_builds:
        raise JiraHandoffError(422, "handoff_export_empty_manifest", "frozen manifest has no builds")

    targets: dict[str, dict[str, str]] = {}
    for item in builds:
        build_name = str(item.get("build") or "").strip()
        if not build_name:
            raise JiraHandoffError(400, "handoff_export_builds_required", "each build target needs build")
        if build_name not in manifest_builds:
            raise JiraHandoffError(
                400,
                "handoff_export_unknown_build",
                f"build {build_name!r} is not present in frozen manifest",
            )
        folder = validate_target_folder_name(str(item.get("target_folder_name") or ""))
        strategy = validate_merge_strategy(str(item.get("merge_strategy") or ""))
        targets[build_name] = {"target_folder_name": folder, "merge_strategy": strategy}

    missing = sorted(set(manifest_builds) - set(targets))
    if missing:
        raise JiraHandoffError(
            400,
            "handoff_export_builds_incomplete",
            "request must include target_folder_name for every build in the frozen manifest",
            payload={"missing_builds": missing},
        )

    root = resolve_export_root(export_root=export_root)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_in_ancestors(root, label="jira handoff export root")

    vprefix = vendor_path_prefix(prefix=vendor_prefix)
    center_raw = (storage_root or "").strip() or resolve_shared_storage_root()
    if not center_raw:
        raise JiraHandoffError(503, "handoff_storage_unconfigured", "shared storage root is not configured")
    center_root = Path(center_raw)
    _assert_no_symlink_in_ancestors(center_root, label="controlled storage root")

    tool_path = Path(aee_extract_tool) if aee_extract_tool else None
    # 延迟解析真实工具：仅当确有补解需求时再 resolve（测试可注入 hook / override）。

    now_utc = datetime.now(timezone.utc)
    ts_compact = now_utc.strftime("%Y%m%d_%H%M%S")
    build_results: list[dict[str, Any]] = []

    # 任一步失败不得写 receipt / 不得发布终稿——用临时态收集，全部成功后再写 xls + receipt。
    prepared: list[dict[str, Any]] = []
    try:
        for build_name in sorted(targets):
            mb = manifest_builds[build_name]
            folder_name = targets[build_name]["target_folder_name"]
            strategy = targets[build_name]["merge_strategy"]
            folder_dir = root / folder_name

            if folder_dir.is_symlink():
                raise JiraHandoffError(
                    400,
                    "handoff_symlink_rejected",
                    f"target folder must not be a symlink: {folder_dir}",
                )
            if strategy == "create_new":
                if folder_dir.exists():
                    raise JiraHandoffError(
                        409,
                        "handoff_target_exists",
                        f"create_new refused because target folder already exists: {folder_name}",
                    )
                folder_dir.mkdir(parents=True, exist_ok=False)
            else:
                if not folder_dir.exists() or not folder_dir.is_dir():
                    raise JiraHandoffError(
                        409,
                        "handoff_target_missing",
                        f"merge_existing requires an existing target folder: {folder_name}",
                    )

            issues = list(mb.get("issues") or [])
            copy_actions: list[dict[str, Any]] = []
            for issue in issues:
                rep = issue["representative"]
                serial = str(rep["serial"])
                entry = str(rep["entry"])
                src = _resolve_center_source(
                    str(rep.get("evidence_location") or ""),
                    storage_root=center_root,
                )
                dest = folder_dir / serial / "aee_exp" / entry
                action = _copy_representative_dir_safe(src, dest, export_root=root)
                # 非 .DEC 一致性核对（复制后）
                src_inv = _inventory_non_dec_files(src)
                dst_inv = _inventory_non_dec_files(dest)
                if src_inv != dst_inv:
                    raise JiraHandoffError(
                        500,
                        "handoff_export_integrity_failed",
                        f"post-copy non-.DEC inventory mismatch for {serial}/{entry}",
                    )
                # 过渡期补解（仅缺失时）
                if tool_path is None and _find_dec_exp_main(dest) is None and _EXTRACT_HOOK is None:
                    tool_path = resolve_aee_extract_tool()
                exp_main = ensure_dec_for_entry(
                    dest,
                    wait_for_lock=wait_for_dec_lock,
                    aee_extract_tool=tool_path,
                )
                copy_actions.append(
                    {
                        "serial": serial,
                        "entry": entry,
                        "action": action,
                        "dec_exp_main": str(exp_main),
                    }
                )

            prepared.append(
                {
                    "build": build_name,
                    "folder_name": folder_name,
                    "folder_dir": folder_dir,
                    "strategy": strategy,
                    "issues": issues,
                    "copy_actions": copy_actions,
                }
            )

        # 全部代表校验通过后，再发布各 build 终稿 .xls
        for item in prepared:
            xls_meta = _publish_final_xls_with_backup(
                item["folder_dir"],
                build=item["build"],
                folder_name=item["folder_name"],
                issues=item["issues"],
                vendor_prefix=vprefix,
                export_root=root,
                ts_compact=ts_compact,
            )
            build_results.append(
                {
                    "build": item["build"],
                    "target_folder_name": item["folder_name"],
                    "merge_strategy": item["strategy"],
                    "representatives": item["copy_actions"],
                    "final_xls": {
                        "filename": xls_meta["filename"],
                        "sha256": xls_meta["sha256"],
                        "size_bytes": xls_meta["size_bytes"],
                        "backups": xls_meta["backups"],
                    },
                    "verified_path_count": len(xls_meta["verified_local_paths"]),
                }
            )
    except JiraHandoffError:
        raise
    except OSError as exc:
        raise JiraHandoffError(500, "handoff_export_io_error", f"export failed: {exc}") from exc

    receipt = {
        "schema": EXPORT_RECEIPT_SCHEMA,
        "schema_version": EXPORT_RECEIPT_SCHEMA_VERSION,
        "handoff_id": clean_id,
        "status": HANDOFF_STATUS_EXPORTED,
        "exported_at": now_utc.isoformat(),
        "exported_by": username or "",
        "manifest_sha256": digest,
        "export_root": str(root),
        "vendor_path_prefix": vprefix,
        "builds": build_results,
    }
    _atomic_write_bytes(bundle_dir / EXPORT_RECEIPT_FILENAME, _canonical_json_bytes(receipt))

    record_audit(
        db,
        action="jira_handoff_bundle_exported",
        resource_type="jira_handoff",
        resource_id=clean_id,
        details={
            "status": HANDOFF_STATUS_EXPORTED,
            "manifest_sha256": digest,
            "builds": [b["build"] for b in build_results],
            "target_folders": [b["target_folder_name"] for b in build_results],
            "final_xls": [b["final_xls"]["filename"] for b in build_results],
        },
        user_id=user_id,
        username=username,
        request=request,
    )
    db.commit()
    return receipt
