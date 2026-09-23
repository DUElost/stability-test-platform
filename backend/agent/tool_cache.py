"""ADR-0033 Phase B 第一切片（#3075）：Agent 侧包拉取与 ``tools_cache`` 校验。

消费链路（C2 单向派生的终端）：Git ``tool_manifest.json`` → 发布工具生成站点派生
副本 ``packages/manifest.json`` + ``packages/{name}/{version}.tar.gz``（中心存储）
→ 本模块拉取到**本机** ``tools_cache/{name}/{version}/`` 并按整包 ``package_sha256``
核验 → 返回包内 ``python``/``script`` 绝对路径给 scan_runner。

纪律：

- **失败即回退，绝不阻断扫描**（C3：env 回退保留一个版本窗口）——任何一步不满足
  都返回 ``None`` 并打一条 ``tool_cache_*`` 日志，scan_runner 沿用原 env 路径。
- **逃生阀默认关**：``STP_DEDUP_SCAN_PACKAGE_REF`` 未设 = 本模块整体 no-op，
  现行为零变化。
- 核验不过（sha 不符 / manifest 无条目 / 已退役 / 包体越界成员）一律拒绝落缓存；
  校验通过的目录写 ``.stp-verified`` 标记，命中即免重拷（幂等，只信 sha 不信 mtime）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

_VERIFY_MARKER = ".stp-verified"


@dataclass(frozen=True)
class PackageTool:
    """解析成功的包：绝对路径的包内解释器与入口脚本。"""

    name: str
    version: str
    python: str
    script: str


def parse_package_ref(ref: str) -> Optional[tuple[str, str]]:
    """纯函数：``"Name/2026.09.22"`` → ``(name, version)``；非法返回 None。"""
    raw = (ref or "").strip()
    if "/" not in raw:
        return None
    name, _, version = raw.partition("/")
    if not name or not version or "/" in version or name in (".", "..") or version in (".", ".."):
        return None
    return name, version


def _entry_for(packages_root: Path, name: str, version: str) -> Optional[dict]:
    """从站点派生副本读登记条目；缺失/退役/坏文件 → None（上层负责回退）。"""
    mf = packages_root / "manifest.json"
    try:
        doc = json.loads(mf.read_text(encoding="utf-8"))
        entries = doc["tools"][name]["versions"]
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("tool_cache_manifest_unreadable path=%s", mf)
        return None
    for e in entries:
        if e.get("version") == version:
            if e.get("retired"):
                logger.warning("tool_cache_entry_retired %s@%s", name, version)
                return None
            return e
    logger.warning("tool_cache_entry_missing %s@%s", name, version)
    return None


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _member_rejection(ti: tarfile.TarInfo) -> Optional[str]:
    """纯函数：成员越界判据。返回拒绝原因或 None（放行）。

    只收普通文件/目录/软链（venv 依赖 ``python → python3`` 链）；硬链、设备、
    FIFO 一律拒。路径必须相对且不含 ``..``。
    """
    name = ti.name
    if name.startswith("/") or any(seg == ".." for seg in Path(name).parts):
        return f"越界路径 {name!r}"
    if ti.issym():
        target = ti.linkname
        if os.path.isabs(target):
            return None  # 指向系统路径（如 /usr/bin/python3）：站点基座约定，PRD 支持矩阵内成立
        if any(seg == ".." for seg in Path(target).parts):
            return f"软链逃逸 {name!r} → {target!r}"
        return None
    if ti.isdir() or ti.isfile():
        return None
    return f"不支持的成员类型 {ti.type!r} @ {name!r}"


def ensure_package(
    name: str, version: str, package_sha256: str, packages_root: Path, cache_root: Path
) -> Optional[Path]:
    """把 ``packages_root`` 中的包拉取并核验到 ``cache_root/{name}/{version}/``。

    返回解出的目录；任何不满足（含 sha 不符）返回 None 且不留下半成品目录。
    """
    dest = cache_root / name / version
    marker = dest / _VERIFY_MARKER
    if marker.exists():
        try:
            if marker.read_text(encoding="utf-8").strip() == package_sha256:
                return dest
            logger.warning("tool_cache_marker_stale %s@%s，重新拉取", name, version)
        except OSError:
            pass
    tarball = packages_root / name / f"{version}.tar.gz"
    if not tarball.is_file():
        logger.warning("tool_cache_tarball_missing %s", tarball)
        return None
    actual = _sha256_of(tarball)
    if actual != package_sha256:
        logger.error(
            "tool_cache_sha_mismatch %s@%s expected=%s actual=%s——拒绝使用，回退 env 路径",
            name, version, package_sha256[:12], actual[:12],
        )
        return None
    cache_root.mkdir(parents=True, exist_ok=True)
    (cache_root / name).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{version}.tmp-", dir=cache_root / name))
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            for ti in tar.getmembers():
                bad = _member_rejection(ti)
                if bad:
                    logger.error("tool_cache_unsafe_member %s@%s: %s——整包拒绝", name, version, bad)
                    return None
            tar.extractall(path=tmp)  # 已全量校验成员，不再逐文件走 filter
        (tmp / _VERIFY_MARKER).write_text(package_sha256 + "\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tmp, dest)
        logger.info("tool_cache_verified %s@%s → %s", name, version, dest)
        return dest
    except (OSError, tarfile.TarError) as exc:
        logger.error("tool_cache_extract_failed %s@%s: %s", name, version, exc)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _packages_root(env: Mapping[str, str]) -> Optional[Path]:
    raw = (env.get("STP_PACKAGES_ROOT") or "").strip()
    if raw:
        return Path(raw)
    aee_nfs = (env.get("STP_AEE_NFS_ROOT") or "").strip()
    if aee_nfs:
        return Path(aee_nfs) / "packages"
    return None


def _cache_root(env: Mapping[str, str]) -> Optional[Path]:
    raw = (env.get("STP_TOOLS_CACHE_ROOT") or "").strip()
    if raw:
        return Path(raw)
    install = (env.get("AGENT_INSTALL_DIR") or "").strip()
    if install:
        return Path(install) / "tools_cache"
    return None


def resolve_packaged_tool(ref_env_key: str, env: Optional[Mapping[str, str]] = None) -> Optional[PackageTool]:
    """按 ``ref_env_key``（值为 ``"Name/version"``）解析包工具：拉取核验成功 → 包内路径，否则 None（回退 env）。

    每个族一个独立引用键（#3075 C6 同款逃生阀：键为空 = 整体 no-op）；
    ADR-0051 Phase 4a 起 scan_runner 与 UnisocScanRunner 共用本泛化入口。
    """
    environ = env if env is not None else os.environ
    parsed = parse_package_ref(environ.get(ref_env_key, ""))
    if not parsed:
        return None
    name, version = parsed
    packages_root = _packages_root(environ)
    cache_root = _cache_root(environ)
    if not packages_root or not cache_root:
        logger.warning("tool_cache_roots_undefined ref=%s——STP_PACKAGES_ROOT/AGENT_INSTALL_DIR 缺失，回退 env", version)
        return None
    entry = _entry_for(packages_root, name, version)
    if not entry:
        return None
    sha = str(entry.get("package_sha256", ""))
    if not (len(sha) == 64 and all(c in "0123456789abcdef" for c in sha.lower())):
        logger.warning("tool_cache_bad_sha %s@%s=%r，回退 env", name, version, sha[:16])
        return None
    pkg_dir = ensure_package(name, version, sha, packages_root, cache_root)
    if not pkg_dir:
        return None
    # ADR-0051 D4：``python: null`` = 包内无解释器，用 Agent 自身解释器。
    python_abs = pkg_dir / str(entry["python"]) if entry.get("python") else Path(sys.executable)
    script_abs = pkg_dir / str(entry.get("script", ""))
    if not python_abs.exists() or not script_abs.is_file():
        logger.error("tool_cache_entry_paths_missing %s@%s python=%s script=%s", name, version, python_abs, script_abs)
        return None
    return PackageTool(name=name, version=version, python=str(python_abs), script=str(script_abs))


def resolve_packaged_scan_tool(env: Optional[Mapping[str, str]] = None) -> Optional[PackageTool]:
    """scan_runner 的既有入口（#3075 C6）：委托泛化 resolver，行为不变。"""
    return resolve_packaged_tool("STP_DEDUP_SCAN_PACKAGE_REF", env)
