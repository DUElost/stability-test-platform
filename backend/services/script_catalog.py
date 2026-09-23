"""Script catalog：从 ``tool_manifest.json`` + 站点包源同步 ``script`` 表（ADR-0051 Phase 3）。

Phase 3 起版本目录已退役：``backend/agent/scripts/<name>/`` 是每族一棵源码树，版本号住
``tool_manifest.json``，发布单元是站点 ``packages/{name}/{version}.tar.gz``。因此注册的输入
不再是「某棵检出的目录」（ADR-0046 病根），而是 **Git 唯一事实源 + 内容寻址包**：

- 对 manifest 里每个平台脚本条目（``python`` 为 null）：打开包源里的 tarball，整包 sha 必须等于
  登记的 ``package_sha256``，再从包内取入口文件 sha（``content_sha256``）、伴随脚本 sha
  （``support_files_manifest``）、``capabilities.json``，登记/复核 ``script`` 行；
- 包不在站点（尚未 ``--publish``）→ 记 ``package_missing``、不建行不改行；
- 已登记行与包内容不一致 → ``conflicts``（包不可变，所以只能是库侧漂移）；``force_rebaseline``
  显式重锚；
- ``retired: true`` 的条目 → 行显式 ``is_active=False``（退役由人经 PR 翻转 manifest，这就是
  ADR-0046 D2 要的「显式动作」）；反之**不再**因「盘上缺失」反激活任何行；
- 活跃行不在 manifest → 只报告 ``unregistered_active``（如历史 seed 行），不动。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.models.script import Script

logger = logging.getLogger(__name__)

_SUPPORTED_SUFFIXES = {
    ".py": "python",
    ".sh": "shell",
}

_CAPABILITIES_FILE = "capabilities.json"
#: ADR-0051 D3：Git 唯一事实源，位于仓库根 / bundle 根（build_bundle 随身复制）。
_DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "tool_manifest.json"
_DEFAULT_CATEGORY = "device"


@dataclass
class ScriptScanResult:
    created: int = 0
    skipped: int = 0
    deactivated: int = 0
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    rebaselined: List[Dict[str, str]] = field(default_factory=list)
    #: ADR-0051：本轮回填 ``package_sha256`` 的行数（Phase 2a 前的行首次遇到登记值）。
    package_backfilled: int = 0
    #: 行上 ``package_sha256`` 与 manifest 登记值不等，或站点 tarball 整包 sha ≠ 登记值。
    package_conflicts: List[Dict[str, str]] = field(default_factory=list)
    #: 已登记但站点包源里没有 tarball（尚未 ``--publish``）。
    package_missing: List[Dict[str, str]] = field(default_factory=list)
    #: 活跃行不在 manifest（历史 seed / 手工登记）——只报告，不反激活。
    unregistered_active: List[Dict[str, str]] = field(default_factory=list)
    #: 因 manifest ``retired: true`` 而显式退役的行。
    deactivated_versions: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "skipped": self.skipped,
            "deactivated": self.deactivated,
            "conflicts": self.conflicts,
            "rebaselined": self.rebaselined,
            "package_backfilled": self.package_backfilled,
            "package_conflicts": self.package_conflicts,
            "package_missing": self.package_missing,
            "unregistered_active": self.unregistered_active,
            "deactivated_versions": self.deactivated_versions,
        }


def detect_script_type(path: Path) -> Optional[str]:
    return _SUPPORTED_SUFFIXES.get(path.suffix.lower())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pick_entry(tree: Path) -> tuple:
    """Return (entry_path, script_type) for the first script file in *tree*（族树入口判据）。"""
    candidates = [
        p for p in sorted(tree.iterdir())
        if p.is_file() and detect_script_type(p) and not p.name.startswith("_")
    ]
    if not candidates:
        return None, None
    entry = candidates[0]
    return entry, detect_script_type(entry)


def support_files_manifest(tree: Path, entry: Path) -> dict[str, str]:
    """Map companion script filenames → sha256 (every script file except *entry*)."""
    manifest: dict[str, str] = {}
    entry_resolved = entry.resolve()
    for path in sorted(tree.iterdir()):
        if not path.is_file():
            continue
        if path.resolve() == entry_resolved:
            continue
        if detect_script_type(path) is None:
            continue
        manifest[path.name] = sha256_file(path)
    return manifest


def read_capabilities(tree: Path) -> list[str]:
    """``capabilities.json`` → 能力列表（#171）；缺失/坏文件 → []。"""
    path = tree / _CAPABILITIES_FILE
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(raw, dict):
        raw = raw.get("capabilities")
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if isinstance(x, str) and x]


def _runtime_path(runtime_root: str | None, packages_root: Path, name: str, version: str, entry_name: str) -> str:
    """Agent 侧路径锚：``{runtime_root}/{name}/v{version}/{entry}``。

    Phase 3 后主机树上已无该目录——Agent 只取 basename 定位包内入口（``script_packages``），
    保留旧形状是为了历史行同构与 Windows 主机的路径根判定。无 runtime_root 时退回包源路径。
    """
    if not runtime_root:
        return str(packages_root / name / f"v{version}" / entry_name)
    normalized_root = runtime_root.rstrip("/\\")
    parts = (name, f"v{version}", entry_name)
    if "\\" in normalized_root or (len(normalized_root) >= 2 and normalized_root[1] == ":"):
        return str(PureWindowsPath(normalized_root, *parts))
    return str(PurePosixPath(normalized_root, *parts))


def load_manifest(manifest_path: str | Path | None) -> dict:
    """读 Git 唯一事实源；缺失/坏文件 → 空文档（调用方按「无登记」处理并记 WARNING）。"""
    if not manifest_path:
        return {"schema_version": 1, "tools": {}}
    path = Path(manifest_path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("script_sync_manifest_unreadable path=%s", path)
        return {"schema_version": 1, "tools": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("tools"), dict):
        return {"schema_version": 1, "tools": {}}
    return doc


def script_entries(doc: dict) -> Iterable[Tuple[str, dict]]:
    """manifest 里的平台脚本条目 ``(name, entry)``（``python`` 为 null 的族；跳过 legacy 名）。"""
    for name, tool in (doc.get("tools") or {}).items():
        versions = (tool or {}).get("versions") or []
        if not any(e.get("python") is None for e in versions):
            continue
        if name in LEGACY_AEE_SCRIPT_NAMES:
            continue
        for entry in versions:
            if isinstance(entry, dict) and entry.get("version"):
                yield str(name), entry


def load_package_index(manifest_path: str | Path | None) -> Dict[Tuple[str, str], str]:
    """``(name, version) → package_sha256``，只收未 retired 的条目（供 verify/presence 等只读消费）。"""
    index: Dict[Tuple[str, str], str] = {}
    for name, entry in script_entries(load_manifest(manifest_path)):
        if entry.get("retired"):
            continue
        sha = entry.get("package_sha256")
        if isinstance(sha, str) and len(sha) == 64:
            index[(name, str(entry.get("version")))] = sha
    return index


def _member_ok(ti: tarfile.TarInfo) -> bool:
    """包成员安全判据（与 ``backend.agent.tool_cache._member_rejection`` 同口径）。"""
    name = ti.name
    if name.startswith("/") or ".." in Path(name).parts:
        return False
    if ti.issym() or ti.islnk() or ti.isdev():
        return False
    return ti.isfile() or ti.isdir()


@dataclass
class PackageFacts:
    entry_name: str
    script_type: str
    content_sha256: str
    support_files_manifest: dict[str, str]
    capabilities: list[str]


def read_package_facts(tarball: Path, expected_sha: str, entry_name: str) -> tuple[Optional[PackageFacts], Optional[str]]:
    """打开站点 tarball：整包 sha 核验 → 解到临时目录 → 取入口/伴随/能力。返回 ``(facts, error)``。"""
    try:
        blob = tarball.read_bytes()
    except OSError:
        return None, "package_missing"
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected_sha:
        return None, "package_sha_mismatch"
    with tempfile.TemporaryDirectory(prefix="stp-script-sync-") as tmp:
        root = Path(tmp)
        try:
            with tarfile.open(tarball, "r:gz") as tar:
                members = [m for m in tar.getmembers() if _member_ok(m)]
                tar.extractall(path=root, members=members)
        except (tarfile.TarError, OSError):
            return None, "package_unreadable"
        entry = root / entry_name
        if not entry.is_file() or detect_script_type(entry) is None:
            return None, "package_entry_missing"
        return PackageFacts(
            entry_name=entry_name,
            script_type=detect_script_type(entry) or "python",
            content_sha256=sha256_file(entry),
            support_files_manifest=support_files_manifest(root, entry),
            capabilities=read_capabilities(root),
        ), None


def default_packages_root() -> Optional[Path]:
    raw = (os.getenv("STP_PACKAGES_ROOT") or "").strip()
    if raw:
        return Path(raw)
    nfs = (os.getenv("STP_AEE_NFS_ROOT") or "").strip()
    return Path(nfs) / "packages" if nfs else None


def sync_scripts_from_manifest(
    db: Session,
    manifest_path: str | Path | None,
    packages_root: str | Path | None,
    runtime_root: str | None = None,
    *,
    force_rebaseline: bool = False,
) -> ScriptScanResult:
    """按 ``tool_manifest.json`` + 站点包源同步 ``script`` 表（见模块 docstring）。"""
    doc = load_manifest(manifest_path if manifest_path is not None else _DEFAULT_MANIFEST_PATH)
    pk_root = Path(packages_root) if packages_root else default_packages_root()
    if pk_root is None:
        raise FileNotFoundError("packages root not configured (STP_PACKAGES_ROOT / STP_AEE_NFS_ROOT)")
    result = ScriptScanResult()
    now = datetime.now(timezone.utc)
    existing_rows = db.query(Script).all()
    existing_by_key = {(row.name, row.version): row for row in existing_rows}
    seen: set[tuple[str, str]] = set()

    for name, entry in script_entries(doc):
        version = str(entry["version"])
        key = (name, version)
        seen.add(key)
        sha = str(entry.get("package_sha256") or "")
        row = existing_by_key.get(key)

        if entry.get("retired"):
            if row is not None and row.is_active:
                row.is_active = False
                row.updated_at = now
                result.deactivated += 1
                result.deactivated_versions.append({"name": name, "version": version, "nfs_path": row.nfs_path or ""})
            continue

        tarball = pk_root / name / f"{version}.tar.gz"
        facts, err = read_package_facts(tarball, sha, str(entry.get("script") or ""))
        if facts is None:
            target = result.package_missing if err == "package_missing" else result.package_conflicts
            target.append({"name": name, "version": version, "reason": err or "unknown", "artifact": str(tarball)})
            if row is not None:
                result.skipped += 1
            continue

        nfs_path = _runtime_path(runtime_root, pk_root, name, version, facts.entry_name)
        if row is None:
            db.add(Script(
                name=name, display_name=name, category=_DEFAULT_CATEGORY,
                script_type=facts.script_type, version=version, nfs_path=nfs_path,
                content_sha256=facts.content_sha256, package_sha256=sha,
                support_files_manifest=facts.support_files_manifest,
                capabilities=facts.capabilities, param_schema={}, default_params={},
                is_active=True, created_at=now, updated_at=now,
            ))
            result.created += 1
            continue

        entry_changed = row.content_sha256 != facts.content_sha256
        support_changed = dict(row.support_files_manifest or {}) != facts.support_files_manifest
        caps_changed = list(row.capabilities or []) != facts.capabilities
        if entry_changed or support_changed or caps_changed:
            if not force_rebaseline:
                result.conflicts.append({"name": name, "version": version})
                continue
            result.rebaselined.append({
                "name": name, "version": version,
                "old_sha256": row.content_sha256 or "", "new_sha256": facts.content_sha256,
            })
            row.content_sha256 = facts.content_sha256
            row.support_files_manifest = facts.support_files_manifest
            row.capabilities = facts.capabilities
            row.package_sha256 = sha
            row.nfs_path = nfs_path
            row.is_active = True
            row.updated_at = now
            continue

        if row.package_sha256 is None:
            row.package_sha256 = sha
            row.updated_at = now
            result.package_backfilled += 1
        elif row.package_sha256 != sha:
            result.package_conflicts.append({
                "name": name, "version": version, "reason": "db_package_sha_mismatch",
                "db_sha256": row.package_sha256, "manifest_sha256": sha,
            })
        if runtime_root and row.nfs_path != nfs_path:
            row.nfs_path = nfs_path
            row.updated_at = now
        result.skipped += 1

    for row in existing_rows:
        if (row.name, row.version) in seen or not row.is_active or row.name in LEGACY_AEE_SCRIPT_NAMES:
            continue
        result.unregistered_active.append({"name": row.name, "version": row.version})

    if result.deactivated_versions:
        logger.warning(
            "script_sync_retired count=%d versions=%s（由 tool_manifest retired:true 显式驱动）",
            result.deactivated, [f"{e['name']}@{e['version']}" for e in result.deactivated_versions[:20]],
        )
    if result.package_missing:
        logger.warning(
            "script_sync_package_missing count=%d（尚未 --publish 到站点包源）first=%s",
            len(result.package_missing), result.package_missing[0],
        )
    db.commit()
    return result
