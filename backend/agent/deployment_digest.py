"""部署 artifact 内容摘要（ADR-0040 D1）——Agent 侧镜像实现。

与控制面 ``backend/services/deployment_digest.py`` **字节级等价**（先例
script_catalog_version 双侧镜像）：算法、排除集、序列化格式必须逐字一致，
等价性测试见 ``backend/tests/services/test_deployment_digest.py``。修改任一
侧必须同步另一侧并更新等价性测试。

本模块只用标准库（Agent 运行形态独立于控制面包结构）。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterator, Optional, Tuple

AGENT_CODE = "agent-code"
HOST_RESOURCES = "host-resources"

_DIR_EXCLUDES = frozenset({"__pycache__", "tests", "venv", "logs"})
_FILE_EXCLUDES = frozenset({
    "VERSION", "ARTIFACT_DIGEST", ".env", ".deps_installed_sha",
    ".env.example", "install_agent.sh", "agentctl.sh", "DEPLOY.md",
    "stability-test-agent.service", "hosts.txt",
})
_SUFFIX_EXCLUDES = (".pyc",)
_TEST_FILE_PREFIX, _TEST_FILE_SUFFIX = "test_", ".py"

_CHUNK = 1 << 20


def _iter_included(root: Path, kind: str) -> Iterator[Tuple[str, bool, Path, os.stat_result]]:
    if kind == HOST_RESOURCES:
        top = root / "resources"
        extra_prune = frozenset({"mtbf"})
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
    entries = list(_iter_included(root, kind))
    for arcname, p in sorted((extra_files or {}).items()):
        st = p.stat()
        entries.append((arcname, bool(st.st_mode & 0o111), p, st))
    entries.sort(key=lambda e: e[0])
    return entries


def compute_artifact_digest(
    root: Path, kind: str = AGENT_CODE, *, extra_files: Optional[dict[str, Path]] = None,
) -> str:
    """计算 artifact 内容摘要——与控制面实现字节级等价。

    ``extra_files`` 语义与控制面侧一致（arcname → 真实路径）。
    """
    lines = "".join(
        f"{rel}\x00{int(exec_bit)}\x00{_file_sha256(p)}\n"
        for rel, exec_bit, p, _st in _collect_entries(Path(root), kind, extra_files)
    )
    return "sha256:" + hashlib.sha256(lines.encode("utf-8")).hexdigest()
