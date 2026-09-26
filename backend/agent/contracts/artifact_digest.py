"""Deployment artifact digest 契约（ADR-0040 D1/D3，ADR-0054 D1）——唯一实现。

ADR-0054 第 3 步后，摘要算法与载荷规范化序列只有这一份实现：

- **算法面**：规范化序列 ``(relpath, 可执行位, content sha256)`` →
  ``digest_entries`` → ``sha256:<hex>``。控制面 ``backend/services/artifact_digest.py``
  直接 import 本模块；控制面自己的输入集枚举（``host_updater._iter_payload_files``）
  按 ADR-0054 §5 第 3 步**留在 services**。
- **枚举面**：``collect_artifact_entries``（agent 载荷树，含排除集）与
  ``collect_control_plane_entries``（控制面 bundle 的 ``backend/**`` 除 agent）是
  本契约对「载荷输入集」的定义，供发布 / 站点安装 / Ansible 工具**按文件路径加载**
  （stdlib-only，不触发 ``backend.*`` 包链，故可在无 DB / 无配置的机器上跑）。

信任模型（ADR-0040 D2）：Agent **不**重算自身树 digest（心跳全树重算被 §7-3
显式否决）——current digest 读部署流程受控写入的 ``ARTIFACT_DIGEST`` 文件
（``version_info.read_artifact_digest``）。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os

DIGEST_PREFIX = "sha256:"

# 载荷排除集（#2030 的「同源」自 ADR-0054 起以**本模块为单一源**）：
# - 控制面 `host_updater` 直接 import 本模块的三个常量（tar 与 digest 同口径）；
# - wrapper `FIXED_EXCLUDES` 与 Ansible `agent_install_excludes` 因「单文件脚本 /
#   YAML 数据」无法 import Python，仍是两份拷贝——由
#   `tests/test_ansible_digest_contract.py` 对四处逐项锁定。
# 语义：#2030 部署通道不传输集 + ADR-0040 D1 的宿主侧目录（venv/logs）+
# ADR-0051 Phase 3 的 scripts（脚本走包分发，不再随源码树同步）；stp_agent_priv.py
# 装到 /usr/local/sbin、stp_schemas/ 经 extra_files 独立附加，均不进安装目录的内容身份。
PAYLOAD_EXCLUDES = {
    "__pycache__",
    # ADR-0051 Phase 3：脚本走包分发（tools_cache），不再随源码树同步；主机上残留的
    # 旧版本目录随 rsync --delete-excluded 清掉。**digest 必须与 tar/rsync 同口径**：
    # 漏掉这一项会让契约侧身份多算 scripts/**（2026-09-26 实证：真实树上两侧 digest
    # 分叉、契约侧多 84 条），Ansible/bundle 写出的身份与控制面 desired 永远不一致。
    "scripts",
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
# Glob 类排除（#2030）：控制面 tar 引用本常量；wrapper / Ansible 侧以同名模式
# （`test_*.py`）参与四处比对，由 tests/test_ansible_digest_contract.py 锁定。
PAYLOAD_EXCLUDE_GLOBS = ("test_*.py",)
PAYLOAD_METADATA_EXCLUDES = {
    "VERSION", "ARTIFACT_DIGEST", "ARTIFACT_DIGEST_RESOURCES", ".env",
}


ARTIFACT_KIND_FULL = "full"
ARTIFACT_KIND_CODE = "code"
ARTIFACT_KIND_RESOURCES = "resources"


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


#: 控制面摘要面（ADR-0051 Phase 4）输入集：bundle 根下 ``backend/**``，
#: **不深入** ``backend/agent/``（那是 agent-code / host-resources 两面的地盘）。
#: 与 agent 面相反，**不排除** ``.env*`` / 字节码——构建 ignore 若被误改，这类文件
#: 一旦进 bundle 必须**改变摘要**（#2269 根因正是「不在任何摘要面内」），而非被豁免。
_CONTROL_PLANE_ROOT = "backend"
_CONTROL_PLANE_SKIP_DIRS = frozenset({"agent"})
#: frontend/dist-prod 属构建产物（另有 provenance 通道），nginx 直读、控制面进程不 import——不进本面。


def collect_control_plane_entries(bundle_root: str) -> list[tuple[str, bool, str]]:
    """``control-plane`` artifact 的规范化序列 ``(relpath, 可执行位, content sha256)``。

    relpath 相对 **bundle 根**（如 ``backend/api/...``）；符号链接跳过（与
    ``collect_artifact_entries`` 一致）；排序后返回，供 ``digest_entries`` 直接摘要。
    双侧共用同一文件：build 端（``tools/release/build_bundle.py``）与 install S0 量具
    （``tools/site_config/install.py``）都从这里取，无镜像分叉面。
    """
    entries: list[tuple[str, bool, str]] = []
    for root, dirs, files in os.walk(os.path.join(bundle_root, _CONTROL_PLANE_ROOT)):
        rel_dir = os.path.relpath(root, bundle_root).replace(os.sep, "/")
        if rel_dir == _CONTROL_PLANE_ROOT:
            dirs[:] = [d for d in dirs if d not in _CONTROL_PLANE_SKIP_DIRS]
        for name in files:
            full_path = os.path.join(root, name)
            if os.path.islink(full_path):
                continue
            relpath = os.path.relpath(full_path, bundle_root).replace(os.sep, "/")
            st = os.stat(full_path)
            h = hashlib.sha256()
            with open(full_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            entries.append((relpath, bool(st.st_mode & 0o111), h.hexdigest()))
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
