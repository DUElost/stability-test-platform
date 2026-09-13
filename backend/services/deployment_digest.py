"""部署 artifact 内容摘要（ADR-0040 D1/D2）——控制面侧实现。

身份 = ``sha256:<hex>``；输入集 = artifact 文件集的规范化序列
``(relpath, 可执行位, 内容 sha256)``（relpath 取 POSIX 分隔、按字典序排序，
``\\x00`` 连接、每文件一行）整体取 sha256。

**双侧镜像**：Agent 侧同算法实现见 ``backend/agent/deployment_digest.py``，
两侧必须字节级等价（等价性测试守护，先例 script_catalog_version）。修改本
文件算法时必须同步镜像侧并在同一 PR 更新等价性测试。

输入集 = 部署流程实际拥有并覆盖的文件集（ADR D1）：

- ``agent-code``：agent 源码树（沿用现行 ``_TAR_EXCLUDES`` 排除集，契约由
  ``test_contract_matches_tarball`` 与 ``_build_tarball`` 对账守护）+
  ``pipeline_schema.json``；**不含 ``resources/``**（分层归属 host-resources，
  ADR D1 表）。
- ``host-resources``：``resources/`` 下非主机本地资产；\`resources/mtbf/\`
  永远属主机本地，不进任何 artifact。
- 两类共同排除主机态/部署态文件：VERSION、ARTIFACT_DIGEST、.env、deps
  marker（``.deps_installed_sha``）、venv、logs、__pycache__（ADR D1）。
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Iterator, Optional, Tuple

AGENT_CODE = "agent-code"
HOST_RESOURCES = "host-resources"
ARTIFACT_KINDS = (AGENT_CODE, HOST_RESOURCES)

# 主机态/部署态与垃圾路径：不参与内容身份（ADR D1）。目录与文件分列，
# 文件集 = 现行 _TAR_EXCLUDES 的文件项 + ADR 增补（对账测试守护）。
_DIR_EXCLUDES = frozenset({"__pycache__", "tests", "venv", "logs"})
_FILE_EXCLUDES = frozenset({
    "VERSION", "ARTIFACT_DIGEST", ".env", ".deps_installed_sha",
    ".env.example", "install_agent.sh", "agentctl.sh", "DEPLOY.md",
    "stability-test-agent.service", "hosts.txt",
})
_SUFFIX_EXCLUDES = (".pyc",)
# 现行 tarball 打包排除的源码测试文件（host_updater._build_tarball 同规则）。
_TEST_FILE_PREFIX, _TEST_FILE_SUFFIX = "test_", ".py"

_CHUNK = 1 << 20


def _iter_included(root: Path, kind: str) -> Iterator[Tuple[str, bool, Path, os.stat_result]]:
    """遍历 artifact 输入集，yield ``(relpath_posix, 可执行位, 路径, stat)``。"""
    if kind == HOST_RESOURCES:
        top = root / "resources"
        extra_prune = frozenset({"mtbf"})  # 主机本地资产（ADR D1）
    elif kind == AGENT_CODE:
        top = root
        extra_prune = frozenset()
    else:
        raise ValueError(f"unknown artifact kind: {kind!r}")
    if not top.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(top):
        rel_dir = os.path.relpath(dirpath, top)
        if kind == AGENT_CODE and rel_dir == ".":
            # 分层（ADR D1）：resources/ 整树归属 host-resources 身份
            dirnames[:] = [d for d in dirnames if d != "resources"]
        dirnames[:] = [d for d in dirnames if d not in _DIR_EXCLUDES and d not in extra_prune]
        for name in filenames:
            if name in _FILE_EXCLUDES or name.endswith(_SUFFIX_EXCLUDES):
                continue
            if kind == AGENT_CODE and name.startswith(_TEST_FILE_PREFIX) and name.endswith(_TEST_FILE_SUFFIX):
                continue
            p = Path(dirpath) / name
            st = p.stat()
            yield p.relative_to(root).as_posix(), bool(st.st_mode & 0o111), p, st


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_entries(
    root: Path, kind: str, extra_files: Optional[dict[str, Path]],
) -> list[Tuple[str, bool, Path, os.stat_result]]:
    """汇总输入集条目（树遍历 + 额外覆盖文件），按 relpath 排序。"""
    entries = list(_iter_included(root, kind))
    for arcname, p in sorted((extra_files or {}).items()):
        st = p.stat()
        entries.append((arcname, bool(st.st_mode & 0o111), p, st))
    entries.sort(key=lambda e: e[0])
    return entries


def _digest_from_entries(
    entries: list[Tuple[str, bool, Path, os.stat_result]],
) -> str:
    lines = "".join(
        f"{rel}\x00{int(exec_bit)}\x00{_file_sha256(p)}\n"
        for rel, exec_bit, p, _st in entries
    )
    return "sha256:" + hashlib.sha256(lines.encode("utf-8")).hexdigest()


def compute_artifact_digest(
    root: Path, kind: str = AGENT_CODE, *, extra_files: Optional[dict[str, Path]] = None,
) -> str:
    """计算 artifact 内容摘要（无缓存纯函数；控制面缓存包装见下）。

    ``extra_files``：部署流程覆盖的树外文件（arcname → 真实路径），如
    ``{"stp_schemas/pipeline_schema.json": ...}``（ADR D1：agent-code 身份
    含 pipeline_schema.json，按其在载荷中的 arcname 参与身份）。
    """
    return _digest_from_entries(_collect_entries(Path(root), kind, extra_files))


# ── 控制面 desired digest 进程缓存（ADR D2：现算 + 进程缓存，缓存键 = 输入集
# 状态；不落 host 表——desired 是控制面 artifact 的属性）。 ──────────────────

_cache_lock = threading.Lock()
_cache: dict[Tuple[str, str, tuple], Tuple[tuple, str]] = {}


def compute_desired_digest(
    root: Path, kind: str = AGENT_CODE, *, extra_files: Optional[dict[str, Path]] = None,
) -> str:
    """控制面 desired digest：输入集状态指纹未变时命中进程缓存。"""
    rootp = Path(root)
    extra = dict(extra_files or {})
    entries = _collect_entries(rootp, kind, extra)
    fingerprint = tuple(sorted(
        (rel, st.st_mtime_ns, st.st_size, st.st_mode) for rel, _e, _p, st in entries
    ))
    key = (str(rootp), kind, tuple(sorted(extra)))
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and hit[0] == fingerprint:
        return hit[1]
    digest = _digest_from_entries(entries)
    with _cache_lock:
        _cache[key] = (fingerprint, digest)
    return digest


def reset_cache() -> None:
    """测试辅助：清空 desired digest 进程缓存。"""
    with _cache_lock:
        _cache.clear()


def digest_input_files(
    root: Path, kind: str = AGENT_CODE, *, extra_files: Optional[dict[str, Path]] = None,
) -> list[str]:
    """输入集文件清单（relpath 排序）——输入集契约对账与诊断用。"""
    return [rel for rel, _e, _p, _st in _collect_entries(Path(root), kind, extra_files)]
