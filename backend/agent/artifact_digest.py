"""Deployment artifact digest（ADR-0040 D1）——Agent 侧镜像实现。

与 ``backend/services/artifact_digest.py`` 构成双侧镜像：**字节级等价**由
``backend/tests/services/test_artifact_digest.py`` 对照测试锁定（先例：
``script_catalog_version`` 双侧实现 + parity test）。

信任模型（ADR-0040 D2）：Agent **不**重算自身树 digest（心跳全树重算被
§7-3 显式否决）——current digest 读部署流程受控写入的 ``ARTIFACT_DIGEST``
文件（``version_info.read_artifact_digest``）。本模块的存在是算法契约的
第二只锚：输入集/序列化若在任一侧漂移，parity test 即红；P2 分层
（``agent-code`` / ``host-resources`` 双 artifact）将复用同一算法。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os

DIGEST_PREFIX = "sha256:"

# 与控制面 _TAR_EXCLUDES / _PAYLOAD_METADATA_EXCLUDES 语义镜像（parity test
# 以同一 fixture 树锁定两侧一致）。#2030：与两条部署通道（wrapper
# FIXED_EXCLUDES / Ansible agent_install_excludes）同源——venv/logs 为宿主侧
# 目录（ADR-0040 D1 明文排除）；stp_agent_priv.py 装到 /usr/local/sbin、
# stp_schemas/ 经 extra_files 独立附加，均不进安装目录的内容身份。
PAYLOAD_EXCLUDES = {
    "__pycache__",
    "tests",
    ".env.example",
    "install_agent.sh",
    "agentctl.sh",
    "DEPLOY.md",
    "stability-test-agent.service",
    "hosts.txt",
    "venv",
    "logs",
    "stp_agent_priv.py",
    "stp_schemas",
    ".deps_installed_sha",
}
PAYLOAD_EXCLUDE_SUFFIXES = (".pyc",)
# Glob 类排除（#2030）：与控制面 `_TAR_EXCLUDE_GLOBS` 逐项镜像，
# 三处排除集同源由 tests/test_ansible_digest_contract.py 锁定。
PAYLOAD_EXCLUDE_GLOBS = ("test_*.py",)
PAYLOAD_METADATA_EXCLUDES = {
    "VERSION", "ARTIFACT_DIGEST", "ARTIFACT_DIGEST_RESOURCES", ".env",
}


ARTIFACT_KIND_FULL = "full"
ARTIFACT_KIND_CODE = "code"
ARTIFACT_KIND_RESOURCES = "resources"
_RESOURCES_PREFIX = "resources/"


def collect_artifact_entries(
    source_dir: str,
    extra_files: dict[str, str] | None = None,
    kind: str = ARTIFACT_KIND_FULL,
) -> list[tuple[str, bool, str]]:
    """规范化序列 ``(relpath, 可执行位, content sha256)``，按 relpath 排序。

    ``extra_files``：``arcname -> 绝对路径``（如 pipeline schema →
    ``stp_schemas/pipeline_schema.json``），镜像控制面载荷枚举的收尾附加。
    ``kind`` 分区（#1963，镜像控制面同款）：``full`` / ``code``（全集 −
    ``resources/**``）/ ``resources``（全集 ∩ ``resources/**``）。
    """
    entries: list[tuple[str, bool, str]] = []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in PAYLOAD_EXCLUDES]
        for name in files:
            if name in PAYLOAD_EXCLUDES:
                continue
            if name.endswith(PAYLOAD_EXCLUDE_SUFFIXES):
                continue
            if any(fnmatch.fnmatch(name, pattern) for pattern in PAYLOAD_EXCLUDE_GLOBS):
                continue
            full_path = os.path.join(root, name)
            if os.path.islink(full_path):
                continue
            relpath = os.path.relpath(full_path, source_dir).replace(os.sep, "/")
            if relpath in PAYLOAD_METADATA_EXCLUDES:
                continue
            if relpath == "resources/mtbf" or relpath.startswith("resources/mtbf/"):
                continue
            if kind == ARTIFACT_KIND_CODE and (
                relpath == "resources" or relpath.startswith("resources/")
            ):
                continue
            if kind == ARTIFACT_KIND_RESOURCES and not (
                relpath == "resources" or relpath.startswith("resources/")
            ):
                continue
            st = os.stat(full_path)
            h = hashlib.sha256()
            with open(full_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            entries.append((relpath, bool(st.st_mode & 0o111), h.hexdigest()))

    if kind != ARTIFACT_KIND_RESOURCES:
        for arcname, path in sorted((extra_files or {}).items()):
            st = os.stat(path)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            entries.append((arcname, bool(st.st_mode & 0o111), h.hexdigest()))

    entries.sort()
    return entries


def digest_entries(entries: list[tuple[str, bool, str]]) -> str:
    """Digest a normalized ``(relpath, exec, sha256)`` sequence → ``sha256:<hex>``.

    纯函数；与控制面镜像实现必须字节级等价（parity test 锁定）。
    """
    payload = json.dumps(
        [[relpath, is_exec, sha] for relpath, is_exec, sha in entries],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()
