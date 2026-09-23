"""ADR-0051 Phase 2b：``script:<name>`` 步骤按 **包身份** 解析到本机 ``tools_cache``。

「DB 权威 → 包身份」这一环（ADR-0051 D4）：``ScriptRegistry`` 解析出的条目若带
``package_sha256``（控制面 scan 从 ``tool_manifest.json`` 回填），且本机开关打开，
就经 ``tool_cache.ensure_package`` 把 ``packages/{name}/{version}.tar.gz`` 拉到
``tools_cache/{name}/{version}/`` 并整包核验，脚本从包内执行；否则（开关关 / 行无包 sha /
拉取核验失败）回退到 ``nfs_path``（Phase 3 前主机树上的版本目录仍在）。

开关 ``STP_SCRIPT_PACKAGES``（Agent 侧；控制面源键 ``STP_AGENT_SCRIPT_PACKAGES`` 经
既有 env 推送链下发）：

- 空 / ``off``（默认）：**逃生阀关**——一律走 ``nfs_path``，不碰包源（无 NFS 读、无告警噪声）；
- ``on``：优先包，失败回退 ``nfs_path``（灰度期；回退会记 WARNING）；
- ``strict``：只走包，失败即步骤失败（Phase 3 删目录后的终态；回退无处可回）。

三处形态耦合的出口（ADR-0051 D4）：cwd = 包解压根；PYTHONPATH 注入的 agent 目录不再由
``nfs_path`` 的 ``parents[3]`` 推导；终止宽限按脚本 **名** 判定。这些在 ``pipeline_engine``
落地，本模块只负责「解析到哪个路径、来源是什么」。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from . import tool_cache

logger = logging.getLogger(__name__)

SWITCH_ENV = "STP_SCRIPT_PACKAGES"
_MODES = ("off", "on", "strict")


class PackageUnavailable(Exception):
    """``strict`` 模式下包不可用（拉取/核验失败或包内无入口）。"""


@dataclass(frozen=True)
class ResolvedScript:
    path: str      #: 实际执行的入口文件绝对路径
    cwd: str       #: 子进程工作目录（包根 / 版本目录）
    source: str    #: ``"package"`` | ``"tree"``
    reason: str = ""  #: 走 tree 的原因（观测用；package 时为空）


def package_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """纯函数：``STP_SCRIPT_PACKAGES`` → ``off|on|strict``（非法值按 off，记一次 WARNING）。"""
    raw = ((env if env is not None else os.environ).get(SWITCH_ENV) or "").strip().lower()
    if not raw:
        return "off"
    if raw in _MODES:
        return raw
    logger.warning("script_packages_bad_mode value=%r → off", raw)
    return "off"


def _entry_basename(nfs_path: str) -> str:
    return os.path.basename((nfs_path or "").replace("\\", "/"))


def resolve_script_path(entry, env: Optional[Mapping[str, str]] = None) -> ResolvedScript:
    """把 ``ScriptEntry`` 解析为可执行路径。

    ``entry`` 只需 ``name`` / ``version`` / ``nfs_path`` / ``package_sha256``（可缺省为 None）。
    永不抛出（``strict`` 例外：抛 ``PackageUnavailable``）。
    """
    environ = env if env is not None else os.environ
    mode = package_mode(environ)
    tree = ResolvedScript(path=entry.nfs_path, cwd=os.path.dirname(entry.nfs_path) or "", source="tree")
    if mode == "off":
        return tree
    sha = getattr(entry, "package_sha256", None)
    if not sha:
        return _fallback(mode, tree, entry, "no_package_sha")

    packages_root = tool_cache._packages_root(environ)
    cache_root = tool_cache._cache_root(environ)
    if not packages_root or not cache_root:
        return _fallback(mode, tree, entry, "roots_undefined")
    pkg_dir = tool_cache.ensure_package(entry.name, entry.version, str(sha), packages_root, cache_root)
    if not pkg_dir:
        return _fallback(mode, tree, entry, "package_unavailable")
    script_abs = Path(pkg_dir) / _entry_basename(entry.nfs_path)
    if not script_abs.is_file():
        return _fallback(mode, tree, entry, "entry_missing_in_package")
    return ResolvedScript(path=str(script_abs), cwd=str(pkg_dir), source="package")


def _fallback(mode: str, tree: ResolvedScript, entry, reason: str) -> ResolvedScript:
    if mode == "strict":
        raise PackageUnavailable(f"{entry.name}@{entry.version}: {reason}")
    logger.warning("script_packages_fallback_tree %s@%s reason=%s", entry.name, entry.version, reason)
    return ResolvedScript(path=tree.path, cwd=tree.cwd, source="tree", reason=reason)


def verify_package(entry: Mapping[str, object], env: Optional[Mapping[str, str]] = None) -> tuple[bool, Optional[str]]:
    """``verify_scripts`` RPC 的包侧判定：``(ok, error)``；``on``/``strict`` 且带 ``package_sha256`` 时才介入。

    成功即已把包预热进 ``tools_cache``（派发前拉包，步骤启动零等待）。
    """
    environ = env if env is not None else os.environ
    sha = entry.get("package_sha256")
    if package_mode(environ) == "off" or not sha:
        return True, None  # 不介入：由调用方按文件 sha 判定
    packages_root = tool_cache._packages_root(environ)
    cache_root = tool_cache._cache_root(environ)
    if not packages_root or not cache_root:
        return False, "package_roots_undefined"
    name, version = str(entry.get("name") or ""), str(entry.get("version") or "")
    pkg_dir = tool_cache.ensure_package(name, version, str(sha), packages_root, cache_root)
    if not pkg_dir:
        return False, "package_unavailable"
    if not (Path(pkg_dir) / _entry_basename(str(entry.get("nfs_path") or ""))).is_file():
        return False, "package_entry_missing"
    return True, None
